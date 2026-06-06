"""Convert Justice Canada XML law bodies to Markdown.

Shared by the batch indexer and the API's live-embed route.
"""

import re
import xml.etree.ElementTree as ET

# Tags that carry amendment history or citation markers — not substantive text.
_SKIP = frozenset({
    'HistoricalNote', 'HistoricalNoteSubItem', 'FootnoteRef', 'Footnote',
})


def _inline(el: ET.Element) -> str:
    """Extract inline text from a mixed-content element with basic formatting."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if child.tag in _SKIP:
            if child.tail:
                parts.append(child.tail)
            continue
        inner = _inline(child)
        if child.tag == 'Emphasis':
            parts.append(f'**{inner}**' if inner else '')
        elif child.tag == 'DefinedTermEn':
            parts.append(f'**{inner}**' if inner else '')
        elif child.tag == 'Sup':
            parts.append(f'^{inner}' if inner else '')
        else:
            parts.append(inner)
        if child.tail:
            parts.append(child.tail)
    return re.sub(r'\s+', ' ', ''.join(parts)).strip()


def _label(el: ET.Element) -> str:
    return (el.findtext('Label') or '').strip()


def _marginal(el: ET.Element) -> str:
    return (el.findtext('MarginalNote') or '').strip()


def _render_table(el: ET.Element) -> str:
    """Convert a CALS TableGroup to a Markdown table."""
    lines: list[str] = []
    for tgroup in el.iter('tgroup'):
        thead = tgroup.find('thead')
        if thead is not None:
            for row in thead.findall('.//row'):
                cells = [_inline(e) for e in row.findall('entry')]
                if cells:
                    lines.append('| ' + ' | '.join(cells) + ' |')
                    lines.append('| ' + ' | '.join('---' for _ in cells) + ' |')
        tbody = tgroup.find('tbody')
        if tbody is not None:
            for row in tbody.findall('row'):
                cells = [_inline(e) for e in row.findall('entry')]
                if cells:
                    lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def _render_formula(el: ET.Element) -> str:
    """Extract text from a FormulaGroup or Formula element."""
    parts = [
        child.text.strip()
        for child in el.iter()
        if child.tag in ('FormulaTerm', 'FormulaText', 'FormulaConnector', 'FormulaDefinition')
        and child.text and child.text.strip()
    ]
    return ' '.join(parts)


def _render_item(el: ET.Element, depth: int) -> str:
    """Render a list-level element (Paragraph, Subparagraph, Clause, Definition, etc.)."""
    indent = '  ' * depth
    lbl = _label(el)
    label_str = f'**{lbl}** ' if lbl else ''

    text_parts: list[str] = []
    child_lines: list[str] = []

    for child in el:
        tag = child.tag
        if tag in ('Label', 'MarginalNote') or tag in _SKIP:
            continue
        if tag == 'Text':
            t = _inline(child)
            if t:
                text_parts.append(t)
        elif tag in ('Paragraph', 'ContinuedParagraph'):
            child_lines.append(_render_item(child, depth + 1))
        elif tag in ('Subparagraph', 'ContinuedSubparagraph'):
            child_lines.append(_render_item(child, depth + 1))
        elif tag == 'Clause':
            child_lines.append(_render_item(child, depth + 1))
        elif tag == 'Subclause':
            child_lines.append(_render_item(child, depth + 2))
        elif tag in ('Definition', 'ContinuedDefinition', 'DefinitionEnOnly'):
            child_lines.append(_render_item(child, depth + 1))
        elif tag == 'TableGroup':
            t = _render_table(child)
            if t:
                child_lines.append(t)
        elif tag in ('FormulaGroup', 'Formula'):
            t = _render_formula(child)
            if t:
                child_lines.append(t)
        elif tag == 'Repealed':
            text_parts.append('[Repealed]')

    text = ' '.join(text_parts)
    leader = f'{indent}- {label_str}{text}'
    if child_lines:
        return leader + '\n' + '\n'.join(child_lines)
    return leader


def _render_subsection(el: ET.Element, blocks: list[str]) -> None:
    lbl = _label(el)
    mg = _marginal(el)

    header_parts: list[str] = []
    if lbl:
        header_parts.append(f'**{lbl}**')
    if mg:
        header_parts.append(f'*{mg}*')

    text_parts: list[str] = []
    child_blocks: list[str] = []

    for child in el:
        tag = child.tag
        if tag in ('Label', 'MarginalNote') or tag in _SKIP:
            continue
        if tag == 'Text':
            t = _inline(child)
            if t:
                text_parts.append(t)
        elif tag in ('Paragraph', 'ContinuedParagraph'):
            child_blocks.append(_render_item(child, 0))
        elif tag in ('Definition', 'ContinuedDefinition', 'DefinitionEnOnly'):
            child_blocks.append(_render_item(child, 0))
        elif tag == 'TableGroup':
            t = _render_table(child)
            if t:
                child_blocks.append(t)
        elif tag in ('FormulaGroup', 'Formula'):
            t = _render_formula(child)
            if t:
                child_blocks.append(t)
        elif tag == 'Repealed':
            child_blocks.append('[Repealed]')

    header = ' '.join(header_parts)
    text = ' '.join(text_parts)
    full = f'{header} {text}'.strip()
    if full:
        blocks.append(full)
    blocks.extend(child_blocks)


def _render_section(el: ET.Element, blocks: list[str]) -> None:
    lbl = _label(el)
    mg = _marginal(el)
    parts = [p for p in [lbl, mg] if p]
    heading = f'### {" — ".join(parts)}' if parts else '###'
    blocks.append(heading)
    for child in el:
        if child.tag in ('Label', 'MarginalNote') or child.tag in _SKIP:
            continue
        _render_body_child(child, blocks)


def _render_body_child(child: ET.Element, blocks: list[str]) -> None:
    """Render one direct child of a Body or Section context."""
    tag = child.tag
    if tag == 'Heading':
        level = int(child.get('level', '1'))
        title = (child.findtext('TitleText') or '').strip()
        if title:
            blocks.append('#' * (level + 1) + f' {title}')
    elif tag in ('Section', 'SectionPiece'):
        _render_section(child, blocks)
    elif tag in ('Subsection', 'ContinuedSectionSubsection'):
        _render_subsection(child, blocks)
    elif tag in ('Paragraph', 'ContinuedParagraph'):
        blocks.append(_render_item(child, 0))
    elif tag in ('Definition', 'ContinuedDefinition', 'DefinitionEnOnly'):
        blocks.append(_render_item(child, 0))
    elif tag == 'Text':
        t = _inline(child)
        if t:
            blocks.append(t)
    elif tag == 'TableGroup':
        t = _render_table(child)
        if t:
            blocks.append(t)
    elif tag in ('FormulaGroup', 'Formula'):
        t = _render_formula(child)
        if t:
            blocks.append(t)
    elif tag == 'Repealed':
        blocks.append('[Repealed]')
    elif tag in ('Note', 'QuotedText', 'ReadAsText'):
        text_el = child.find('Text')
        if text_el is not None:
            t = _inline(text_el)
            if t:
                blocks.append(f'> {t}')


def body_to_markdown(root: ET.Element) -> str:
    """Convert the Body of an act or regulation XML element to Markdown."""
    body = root.find('Body')
    if body is None:
        return ''
    blocks: list[str] = []
    for child in body:
        if child.tag not in _SKIP:
            _render_body_child(child, blocks)
    return '\n\n'.join(b for b in blocks if b.strip())
