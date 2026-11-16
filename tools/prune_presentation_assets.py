"""Remove unused template layouts/media while preserving every used slide and asset."""
import argparse
import posixpath
import re
import zipfile
from pathlib import Path
from lxml import etree as E

R = 'http://schemas.openxmlformats.org/package/2006/relationships'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
O = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def target(owner, relative):
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), relative)).lstrip('/')


def relationships(owner):
    return posixpath.join(posixpath.dirname(owner), '_rels', posixpath.basename(owner) + '.rels') if owner else '_rels/.rels'


def prune(source, destination):
    if Path(destination).exists():
        raise ValueError('Refusing to overwrite an existing optimized deck')
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    original = dict(parts)
    slides = [n for n in parts if re.fullmatch(r'ppt/slides/slide\d+.xml', n)]
    used_layouts = set()
    for slide in slides:
        for relation in E.fromstring(parts[relationships(slide)]):
            if relation.get('Type', '').endswith('/slideLayout'):
                used_layouts.add(target(slide, relation.get('Target')))
    for master in [n for n in parts if re.fullmatch(r'ppt/slideMasters/slideMaster\d+.xml', n)]:
        rel_name = relationships(master)
        rels = E.fromstring(parts[rel_name])
        removed = set()
        for relation in list(rels):
            if relation.get('Type', '').endswith('/slideLayout') and target(master, relation.get('Target')) not in used_layouts:
                removed.add(relation.get('Id'))
                rels.remove(relation)
        document = E.fromstring(parts[master])
        for element in list(document.iter(f'{{{P}}}sldLayoutId')):
            if element.get(f'{{{O}}}id') in removed:
                element.getparent().remove(element)
        parts[master] = E.tostring(document, encoding='UTF-8', xml_declaration=True, standalone=True)
        parts[rel_name] = E.tostring(rels, encoding='UTF-8', xml_declaration=True, standalone=True)
    reachable, queue = set(), ['']
    while queue:
        owner = queue.pop()
        if owner:
            reachable.add(owner)
        rel_name = relationships(owner)
        if rel_name not in parts:
            continue
        reachable.add(rel_name)
        for relation in E.fromstring(parts[rel_name]):
            if relation.get('TargetMode') == 'External':
                continue
            part = target(owner, relation.get('Target'))
            if part not in parts:
                raise ValueError(f'Broken package relationship: {part}')
            if part not in reachable:
                reachable.add(part)
                queue.append(part)
    types = E.fromstring(parts['[Content_Types].xml'])
    for override in list(types):
        if override.tag.endswith('Override') and override.get('PartName').lstrip('/') not in reachable:
            types.remove(override)
    reachable.add('[Content_Types].xml')
    parts['[Content_Types].xml'] = E.tostring(types, encoding='UTF-8', xml_declaration=True, standalone=True)
    if not all(original[n] == parts[n] for n in slides):
        raise ValueError('Optimization changed slide content')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(reachable):
            archive.writestr(name, parts[name])
    print(f'Preserved {len(slides)} slides and {len(used_layouts)} used layouts; removed {len(original)-len(reachable)} unused parts')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    prune(args.source, args.destination)
