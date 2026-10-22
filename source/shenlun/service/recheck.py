"""Contested-annotation recheck: one extra call, stored beside the review.

The service only builds the payload and validates verdicts; persistence lives on
``Store.save_review_recheck`` so the original review bytes never change.
"""
from __future__ import annotations

import copy

from .. import recheck as rules
from .prompts import RECHECK_SCHEMA, RECHECK_SYSTEM, RECHECK_VERSION
from .text import _notify


class RecheckMixin:
    def recheck_payload(self, review, indices):
        """The exact payload used for both the fee estimate and the real call."""
        if not isinstance(review, dict):
            raise ValueError('请先载入训练报告。')
        submission = review.get('submission') if isinstance(review.get('submission'), dict) else {}
        assessment = review.get('assessment') if isinstance(review.get('assessment'), dict) else {}
        annotations = assessment.get('annotations') if isinstance(assessment.get('annotations'), list) else []
        chosen = rules.select_indices(annotations, indices)
        selected = []
        for index in chosen:
            item = annotations[index] if isinstance(annotations[index], dict) else {}
            selected.append({'index': index, 'quote': item.get('quote', ''), 'issue': item.get('issue', ''),
                             'issue_type': item.get('issue_type', ''), 'dimension': item.get('dimension', ''),
                             'suggestion': item.get('suggestion', ''),
                             'material_refs': item.get('material_refs', [])})
        return {'question': submission.get('question', ''),
                'question_type': submission.get('question_type', ''),
                'max_score': submission.get('max_score'),
                'materials': submission.get('materials', ''),
                'answer': submission.get('answer', ''),
                'dimensions': [{key: item.get(key) for key in ('id', 'name', 'score', 'weight', 'reason')}
                               for item in assessment.get('dimensions', []) if isinstance(item, dict)],
                'material_points': [item.get('point', '') for item in assessment.get('material_points', [])
                                    if isinstance(item, dict)],
                'annotations': selected, 'output_schema': copy.deepcopy(RECHECK_SCHEMA)}

    def recheck_annotations(self, review, indices, progress=None, cancel=None):
        """Recheck only the chosen annotations; never re-scores the whole answer."""
        payload = self.recheck_payload(review, indices)
        chosen = [item['index'] for item in payload['annotations']]
        _notify(progress, '正在复核 %d 条批注；只核对所选条目，不重新评分…' % len(chosen))
        response = self._complete(RECHECK_SYSTEM, payload, cancel, prompt_version=RECHECK_VERSION)
        verdicts = rules.validate_verdicts(response['data'], review, chosen)
        return {'review_id': review.get('id', ''), 'indices': chosen,
                'verdicts': {str(index): verdict for index, verdict in verdicts.items()},
                'model': response.get('model', self.config['model']),
                'prompt_version': RECHECK_VERSION, 'usage': dict(response['usage'])}

    def save_recheck(self, result):
        """Persist a finished recheck; the review row is untouched."""
        if not isinstance(result, dict):
            raise ValueError('复核结果格式不正确。')
        return self.store.save_review_recheck(result.get('review_id', ''), result)
