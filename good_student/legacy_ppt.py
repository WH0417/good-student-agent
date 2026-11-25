"""Read current-slide text in legacy PPT files without running Office.

Follows Microsoft MS-PPT record headers, persist directories and TextChars/
TextBytes atoms. Shapes and images remain in the preserved original document.
"""
import struct


def _records(data, start, end):
    while start + 8 <= end:
        options, kind, length = struct.unpack_from("<HHI", data, start)
        payload, stop = start + 8, start + 8 + length
        if stop > end or stop > len(data):
            raise ValueError("旧版 PPT 的记录长度异常")
        yield options, kind, payload, stop
        start = stop


def _texts(data, start, end, depth=0):
    if depth > 64:
        raise ValueError("旧版 PPT 嵌套过深")
    for options, kind, payload, stop in _records(data, start, end):
        if kind == 4000:
            if (stop - payload) % 2:
                raise ValueError("PPT Unicode 文本长度异常")
            text = data[payload:stop].decode("utf-16-le")
            if text.strip():
                yield text.replace("\r", "\n").replace("\v", "\n")
        elif kind == 4008:
            text = data[payload:stop].decode("latin1")
            if text.strip():
                yield text.replace("\r", "\n").replace("\v", "\n")
        elif options & 15 == 15 or kind == 0xF00D:
            yield from _texts(data, payload, stop, depth + 1)


def read_legacy_ppt(path):
    import olefile
    with olefile.OleFileIO(str(path)) as package:
        data = package.openstream("PowerPoint Document").read()
        user = package.openstream("Current User").read()
    if len(user) < 20:
        raise ValueError("PPT 缺少当前编辑位置")
    edit = struct.unpack_from("<I", user, 16)[0]
    mapping, visited = {}, set()
    document_id = None
    while edit and edit not in visited:
        visited.add(edit)
        if edit + 32 > len(data):
            raise ValueError("PPT 当前编辑索引损坏")
        previous, directory, current_doc = struct.unpack_from("<III", data, edit + 16)
        if document_id is None:
            document_id = current_doc
        if directory + 8 > len(data):
            raise ValueError("PPT 持久化索引损坏")
        _, record_type, length = struct.unpack_from("<HHI", data, directory)
        if record_type not in (6001, 6002):
            raise ValueError("无法读取 PPT 持久化索引")
        pos, end = directory + 8, directory + 8 + length
        if end > len(data):
            raise ValueError("PPT 持久化索引越界")
        while pos + 4 <= end:
            value = struct.unpack_from("<I", data, pos)[0]
            first, count = value & 0xFFFFF, value >> 20
            pos += 4
            if pos + count * 4 > end:
                raise ValueError("PPT 索引条目越界")
            for number in range(count):
                mapping.setdefault(first + number, struct.unpack_from("<I", data, pos + number * 4)[0])
            pos += count * 4
        edit = previous
    document_offset = mapping.get(document_id)
    if document_offset is None:
        raise ValueError("找不到当前 PPT 文档")
    _, kind, length = struct.unpack_from("<HHI", data, document_offset)
    if kind != 1000:
        raise ValueError("当前 PPT 文档类型异常")
    slides = []
    for options, record_type, payload, stop in _records(data, document_offset + 8, document_offset + 8 + length):
        if record_type != 4080 or options >> 4 != 0:
            continue
        current = None
        for child_options, child_type, child_start, child_end in _records(data, payload, stop):
            if child_type == 1011:
                current = {"persist_id": struct.unpack_from("<I", data, child_start)[0], "texts": []}
                slides.append(current)
            elif current is not None and child_type in (4000, 4008):
                encoding = "utf-16-le" if child_type == 4000 else "latin1"
                current["texts"].append(data[child_start:child_end].decode(encoding).replace("\r", "\n").replace("\v", "\n"))
    pages = []
    for number, slide in enumerate(slides, 1):
        texts = slide["texts"][:]
        offset = mapping.get(slide["persist_id"])
        if offset is not None and offset + 8 <= len(data):
            _, _, length = struct.unpack_from("<HHI", data, offset)
            texts.extend(_texts(data, offset + 8, offset + 8 + length))
        texts = list(dict.fromkeys(text for text in texts if text.strip()))
        pages.append({"number": number, "text": "\n".join(texts)})
    if not pages:
        raise ValueError("未能读取当前幻灯片列表")
    return pages
