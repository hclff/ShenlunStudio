"""Small original vector glyphs; no icon font or remote asset dependency."""
from PySide6.QtCore import QByteArray,Qt
from PySide6.QtGui import QIcon,QPainter,QPixmap
from PySide6.QtSvg import QSvgRenderer


PATHS={
 'edit':'<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z"/><path d="M13 20h7"/>',
 'library':'<path d="M4 4h5v16H4zM10 4h5v16h-5zM17 5l4-1 3 15-4 1z"/>',
 'methods':'<path d="m12 3 9 5-9 5-9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
 'history':'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
 'settings':'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
 'help':'<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01"/>',
 'import':'<path d="M4 15v5h16v-5M12 3v12m-5-5 5 5 5-5"/>',
 'save':'<path d="M4 3h13l4 4v14H3V3zM7 3v6h10V3M7 21v-8h10v8"/>',
 'arrow':'<path d="M5 12h14m-5-5 5 5-5 5"/>',
 'check':'<path d="m5 12 4 4L19 6"/>',
 'close':'<path d="m6 6 12 12M18 6 6 18"/>',
 'book':'<path d="M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3ZM12 6v16M6 9h3M6 13h3M15 9h3M15 13h3"/>',
}


def icon(name,color='#64748b',size=24):
    path=PATHS.get(name,PATHS['book'])
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 26 24"><g fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{path}</g></svg>'
    pixmap=QPixmap(size*2,size*2);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);QSvgRenderer(QByteArray(svg.encode())).render(painter);painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def app_icon():
    svg=b'''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect x="8" y="8" width="240" height="240" rx="54" fill="#132b43"/><path d="M55 62h59l14 12 14-12h59v130h-59l-14 12-14-12H55Z" fill="none" stroke="#edf5fc" stroke-width="11" stroke-linejoin="round"/><path d="M128 75v128M76 97h29M76 124h29M151 97h29M151 124h29" fill="none" stroke="#edf5fc" stroke-width="9" stroke-linecap="round"/><path d="m155 174 18 17 35-43" fill="none" stroke="#4dd6bd" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/></svg>'''
    result=QIcon()
    for size in (24,32,48,64,128,256):
        pixmap=QPixmap(size,size);pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap);QSvgRenderer(QByteArray(svg)).render(painter);painter.end()
        result.addPixmap(pixmap)
    return result
