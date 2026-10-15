"""Translate saved note anchors through keyboard edits without changing comments."""
from bisect import bisect_left
from difflib import SequenceMatcher
from content_edits import projection, project_note, original_range, length


def offsets(value):
    result = [0]
    for c in value: result.append(result[-1] + length(c))
    return result


def reanchor(before, after, notes, stamp):
    if not notes: return []
    old, old_map = projection(before); new, new_map = projection(after)
    old_offsets, new_offsets = offsets(old), offsets(new)
    changes = SequenceMatcher(None, old, new, autojunk=max(len(old),len(new))>20000).get_opcodes()
    def position(offset, ending):
        index = bisect_left(old_offsets, offset); candidates = []
        for tag, a, b, c, d in changes:
            if tag == 'equal' and a <= index <= b: candidates.append(c + index - a)
            elif a == b == index: candidates.append(c if ending else d)
            elif a <= index <= b:
                candidates.append(c if index == a else d if index == b else (d if ending else c))
        return new_offsets[(min(candidates) if ending else max(candidates)) if candidates else len(new)]
    result = []
    for note in notes:
        visible = project_note(note, old, old_map)
        if not visible:
            result.append({**note, 'deleted_at': note['deleted_at'] or stamp}); continue
        a, b = position(visible['start'],False), position(visible['end'],True)
        if a >= b:
            result.append({**note,'deleted_at':note['deleted_at'] or stamp}); continue
        a,b = original_range(new_map,a,b)
        quote = after['body'].encode('utf-16-le')[a*2:b*2].decode('utf-16-le')
        result.append({**note,'start':a,'end':b,'quote':quote})
    return result
