"""Advisory port declarations. Phase 1 performs no runtime conversions."""
TYPES=frozenset({'string','number','boolean','object','array','array<string>','array<object>'})


def valid_type(name):
    return isinstance(name,str) and name in TYPES


def compatibility(source,target):
    if not valid_type(source) or not valid_type(target):return 'mismatch'
    if source==target:return 'ok'
    if source=='string' or target=='string':return 'coerce'
    if source.startswith('array<') and target=='array':return 'ok'
    if source=='array' and target.startswith('array<'):return 'coerce'
    return 'mismatch'
