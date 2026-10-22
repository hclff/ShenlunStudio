"""Every supported document format yields the same title/author/body shape."""
import zipfile

import pytest

from shenlun.document_readers import read_document, read_json_articles


CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles></container>')

OPF = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>海风与治理</dc:title><dc:creator>张九</dc:creator><dc:date>2025-06-01</dc:date>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>'''

CHAPTER = ('<?xml version="1.0" encoding="utf-8"?><html '
           'xmlns="http://www.w3.org/1999/xhtml"><body><h1>{head}</h1>'
           '<p>{first}</p><p>{second}</p></body></html>')


def build_epub(path):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('META-INF/container.xml', CONTAINER)
        archive.writestr('OEBPS/content.opf', OPF)
        archive.writestr('OEBPS/nav.xhtml', CHAPTER.format(head='目录', first='导航一', second='导航二'))
        archive.writestr('OEBPS/text/ch1.xhtml',
                         CHAPTER.format(head='第一章', first='基层治理要靠制度。', second='也要靠人。'))
        archive.writestr('OEBPS/text/ch2.xhtml',
                         CHAPTER.format(head='第二章', first='第二段落。', second='尾段。'))
    return path


def test_markdown_front_matter_and_heading_are_metadata(tmp_path):
    path = tmp_path / '素材.md'
    path.write_text('---\ntitle: 申论素材\nauthor: 王五\ndate: 2025-07-08\n'
                    'tags: 生态、民生\n---\n# 正文标题\n\n第一段。\n第二段。',
                    encoding='utf-8')
    document = read_document(path)
    assert document['title'] == '申论素材' and document['author'] == '王五'
    assert document['publication'] == '2025-07-08 00:00:00'
    assert document['tags'] == ['生态', '民生']
    assert document['paragraphs'][:2] == ['# 正文标题', '第一段。']
    assert '第一段。' in document['body']


def test_markdown_heading_becomes_the_title_without_front_matter(tmp_path):
    path = tmp_path / 'note.md'
    path.write_text('# 以标题为准\n\n段落。', encoding='utf-8')
    document = read_document(path)
    assert document['title'] == '以标题为准'
    assert document['paragraphs'] == ['段落。']


def test_txt_falls_back_to_the_file_name(tmp_path):
    path = tmp_path / '随手记.txt'
    path.write_text('第一段。\n\n第二段。', encoding='utf-8-sig')
    document = read_document(path)
    assert document['title'] == '随手记' and document['format'] == 'txt'
    assert document['paragraphs'] == ['第一段。', '第二段。']


def test_docx_reads_core_properties_and_paragraphs(tmp_path):
    docx = pytest.importorskip('docx')
    path = tmp_path / '报告.docx'
    document = docx.Document()
    document.core_properties.title = '治理现代化的抓手'
    document.core_properties.author = '李四'
    document.add_heading('一、总论', level=1)
    document.add_paragraph('第一段。')
    document.add_paragraph('第二段。')
    document.save(path)
    parsed = read_document(path)
    assert parsed['title'] == '治理现代化的抓手'
    assert parsed['author'] == '李四'
    assert parsed['format'] == 'docx'
    assert parsed['paragraphs'] == ['第一段。', '第二段。']


def test_epub_follows_the_spine_and_ignores_the_nav_document(tmp_path):
    document = read_document(build_epub(tmp_path / 'book.epub'))
    assert (document['title'], document['author']) == ('海风与治理', '张九')
    assert document['publication'] == '2025-06-01 00:00:00'
    assert document['format'] == 'epub'
    assert document['paragraphs'][0] == '第一章'
    assert '基层治理要靠制度。' in document['paragraphs']
    assert '导航一' not in document['paragraphs']


def test_broken_epub_reports_a_readable_reason(tmp_path):
    path = tmp_path / 'broken.epub'
    path.write_text('not a zip', encoding='utf-8')
    with pytest.raises(ValueError, match='EPUB'):
        read_document(path)


def test_blank_pdf_reports_that_ocr_is_needed(tmp_path):
    pypdf = pytest.importorskip('pypdf')
    path = tmp_path / 'scan.pdf'
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    with pytest.raises(ValueError, match='文字|OCR'):
        read_document(path)


def test_unsupported_and_oversized_files_are_rejected(tmp_path):
    other = tmp_path / 'sheet.xlsx'
    other.write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='TXT'):
        read_document(other)
    huge = tmp_path / 'huge.txt'
    with huge.open('wb') as output:
        output.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='25'):
        read_document(huge)
    listed = tmp_path / 'items.json'
    listed.write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='JSON'):
        read_document(listed)


def test_json_articles_accepts_list_and_wrapper(tmp_path):
    listed = tmp_path / 'list.json'
    listed.write_text('[{"title":"甲","body":"正文"}]', encoding='utf-8')
    assert read_json_articles(listed)[0]['title'] == '甲'
    wrapped = tmp_path / 'wrapped.json'
    wrapped.write_text('{"articles":[{"title":"乙","body":"正文"}]}', encoding='utf-8')
    assert read_json_articles(wrapped)[0]['title'] == '乙'
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{"issues": []}', encoding='utf-8')
    with pytest.raises(ValueError, match='清单'):
        read_json_articles(manifest)
    with pytest.raises(ValueError, match='找不到'):
        read_json_articles(tmp_path / 'missing.json')
