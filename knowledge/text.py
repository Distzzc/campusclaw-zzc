"""Text extraction with source locations and tokenizer-bounded windows."""
import hashlib
import json
import re
from pathlib import Path


class ExtractionError(ValueError):
    pass


def extract(path, filename):
    suffix = Path(filename).suffix.lower()
    units = []
    if suffix in ('.txt', '.md'):
        text = Path(path).read_text(encoding='utf-8-sig')
        headings = []
        for number, paragraph in enumerate(re.split(r'\n\s*\n', text), 1):
            if suffix == '.md':
                for line in paragraph.splitlines():
                    match = re.match(r'^(#{1,6})\s+(.+)', line)
                    if match:
                        depth = len(match[1])
                        headings = headings[:depth - 1] + [match[2]]
            if paragraph.strip():
                units.append((paragraph, {'paragraph': number, 'headings': list(headings)}))
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ''
            if text.strip():
                units.append((text, {'page': number}))
    elif suffix == '.docx':
        from docx import Document
        headings = []
        document = Document(path)
        # iter_inner_content preserves paragraph/table order in the document.
        for number, block in enumerate(document.iter_inner_content(), 1):
            if hasattr(block, 'rows'):
                text = '\n'.join('\t'.join(cell.text for cell in row.cells) for row in block.rows)
            else:
                text = block.text
                style = block.style.name if block.style else ''
                if style.startswith('Heading ') and style[8:].isdigit():
                    headings = headings[:int(style[8:]) - 1] + [text]
            if text.strip():
                units.append((text, {'paragraph': number, 'headings': list(headings)}))
    else:
        raise ExtractionError('不支持该文件格式的正文索引')
    if not units:
        raise ExtractionError('材料没有可读取的正文；扫描件需要先转换为文本')
    return units


def split_units(units, tokenizer, max_tokens, material_id, model_key):
    """Use original character slices, never tokenizer.decode (which alters text)."""
    special = tokenizer.num_special_tokens_to_add(pair=False)
    budget = max_tokens - special
    if budget < 8:
        raise ValueError('embedding token budget too small')
    version = hashlib.sha256(json.dumps(
        [units, model_key, max_tokens, 'split-v1', 'jieba-0.42.1'],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    chunks = []
    for text, location in units:
        start = 0
        while start < len(text):
            # Find a safe character window. Retokenize the exact slice to prevent
            # boundary effects from exceeding the embedding input length.
            lo, hi = start + 1, len(text)
            end = lo
            while lo <= hi:
                mid = (lo + hi) // 2
                count = len(tokenizer.encode(text[start:mid], add_special_tokens=False))
                if count <= budget:
                    end, lo = mid, mid + 1
                else:
                    hi = mid - 1
            value = text[start:end]
            if len(tokenizer.encode(value, add_special_tokens=False)) > budget:
                raise ValueError('character exceeds token budget')
            ordinal = len(chunks) + 1
            chunk_id = hashlib.sha256(f'{material_id}:{version}:{ordinal}'.encode()).hexdigest()
            chunks.append({'id': chunk_id, 'text': value, 'ordinal': ordinal,
                           'location': dict(location, chunk=ordinal, char_start=start, char_end=end),
                           'content_version': version})
            if end == len(text):
                break
            overlap = max(1, budget // 10)
            back = end
            while back > start + 1:
                if len(tokenizer.encode(text[back-1:end], add_special_tokens=False)) > overlap:
                    break
                back -= 1
            start = max(start + 1, back)
    return version, chunks


def terms(text):
    import jieba
    return list(dict.fromkeys(word.casefold() for word in jieba.cut_for_search(text)
                             if re.search(r'\w', word, flags=re.UNICODE)))
