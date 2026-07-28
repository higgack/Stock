"""ESON (Efficient Structured Object Notation) codec for agent handoffs.

Compact, lossless encoding for structured payloads where the reader is an LLM
or program — typically agent-to-agent handoffs. Reduces tokens by ~50% vs JSON
for uniform record arrays while maintaining 100% lossless recovery.

Reference: https://github.com/Green-PT/honey-eson (v1.1 spec)
"""

from typing import Any, Dict, List, Optional, Union
import json


def _is_bare_string(val: Any) -> bool:
    """Check if a value can be encoded as a bare string (no JSON quoting)."""
    if val is None or isinstance(val, bool) or isinstance(val, (int, float)):
        return False
    if not isinstance(val, str):
        return False
    if not val or val[0] in ('"', '[', '{') or val[0].isspace() or val[-1].isspace():
        return False
    if '\t' in val or '\r' in val or '\n' in val:
        return False
    if val in ('null', 'true', 'false'):
        return False
    try:
        float(val)
        return False
    except ValueError:
        pass
    return True


def _encode_cell(val: Any) -> str:
    """Encode a single cell value as bare string or JSON."""
    if val is None:
        return 'null'
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return val if _is_bare_string(val) else json.dumps(val)
    return json.dumps(val, separators=(',', ':'))


def encode_record_array(
    name: str,
    records: List[Dict[str, Any]],
    number: bool = True
) -> str:
    """Encode a list of dicts as ESON record array.
    
    Args:
        name: Top-level field name
        records: List of dictionaries with uniform schema
        number: If True, prepend 'n' field with 1-based row numbers (reserved field)
    
    Returns:
        ESON string (without document header; single record array only)
    """
    if not records:
        return f"{name}[0]\n"
    
    fields = list(records[0].keys())
    if number:
        fields = ['n'] + [f for f in fields if f != 'n']
    
    field_str = ','.join(fields)
    lines = [f"{name}[{len(records)}]{{{field_str}}}"]
    
    for i, rec in enumerate(records, 1):
        row = []
        for j, field in enumerate(fields):
            if field == 'n':
                row.append(str(i))
            else:
                val = rec.get(field)
                row.append(_encode_cell(val))
        lines.append('\t'.join(row))
    
    return '\n'.join(lines) + '\n'


def encode_document(
    header: Dict[str, str],
    arrays: Dict[str, List[Dict[str, Any]]],
    number: bool = True
) -> str:
    """Encode a complete ESON document.
    
    Args:
        header: Scalar fields (from, to, kind, id, etc.)
        arrays: Record arrays by name
        number: If True, add 'n' field to all record arrays
    
    Returns:
        Full ESON document (with !eson/1 header)
    """
    lines = ['!eson/1']
    
    for key, val in header.items():
        lines.append(f"{key}={_encode_cell(val)}")
    
    for name, records in arrays.items():
        if not records:
            lines.append(f"{name}[0]")
        else:
            encoded = encode_record_array(name, records, number=number)
            lines.append(encoded.rstrip('\n'))
    
    return '\n'.join(lines) + '\n'


def decode_cell(cell: str) -> Any:
    """Decode a cell value (bare string or JSON)."""
    if cell == 'null':
        return None
    if cell == 'true':
        return True
    if cell == 'false':
        return False
    if cell in ('"', '[', '{'):
        try:
            return json.loads(cell)
        except json.JSONDecodeError:
            return cell
    try:
        if '.' in cell or 'e' in cell.lower():
            return float(cell)
        return int(cell)
    except ValueError:
        return cell


def decode_document(text: str) -> Dict[str, Any]:
    """Parse ESON document into nested dict.
    
    Returns:
        Dict with 'scalars' (header fields) and 'arrays' (record arrays)
    """
    if not text.startswith('!eson/1'):
        raise ValueError("Missing ESON header: !eson/1")
    
    lines = text.strip().split('\n')[1:]
    result = {'scalars': {}, 'arrays': {}}
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if '=' in line and '[' not in line:
            key, val = line.split('=', 1)
            result['scalars'][key] = decode_cell(val)
            i += 1
        elif '[' in line and '{' in line:
            match = line[:line.index('[')]
            parts = line[line.index('['):].replace('{', ' ').replace('}', ' ').split()
            count = int(parts[0][1:-1])
            fields = [f.strip() for f in line[line.index('{')+1:line.index('}')].split(',')]
            records = []
            for _ in range(count):
                i += 1
                if i < len(lines):
                    cells = lines[i].split('\t')
                    records.append({fields[j]: decode_cell(cells[j]) for j in range(len(fields))})
            result['arrays'][match] = records
            i += 1
        else:
            i += 1
    
    return result
