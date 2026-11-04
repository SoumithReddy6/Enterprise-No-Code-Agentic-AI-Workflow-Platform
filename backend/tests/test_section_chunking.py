from backend.app.kb.ingestion import chunk_pages

CONFIG={'chunking':'section','chunk_size':250,'chunk_overlap':30}

def test_section_heading_and_parent_stem_survive_page_boundary():
    pages=['# Expense policy\n\n## Accountable plans\n\nReturn excess advances within a reasonable period.\n',
           'Reasonable period of time. The following actions qualify:\n• Account for expenses within 60 days.\n• Return excess reimbursement within 120 days.']
    chunks=chunk_pages(pages,CONFIG)
    passage=next(c for c in chunks if '120 days' in c['text'])
    assert 'Accountable plans' in passage['heading_path']
    assert '60 days' in passage['text']
    assert passage['page']==2

def test_nested_regulatory_heading_keeps_subject_and_driving_time():
    pages=['§ 395.3 Maximum driving time for property-carrying vehicles.\n\n(a) A driver must comply with these requirements:\n\n(1) Start of work shift. Take 10 hours off duty.\n\n(3) Driving time—(i) Driving time. A driver may drive 11 hours.\n\n§ 395.5 Passenger-carrying vehicles.\n\n(a) A driver may drive 10 hours.']
    chunks=chunk_pages(pages,CONFIG)
    c=next(c for c in chunks if '11 hours' in c['text'])
    assert c['heading_path'][0].startswith('§ 395.3')
    assert 'Driving time' in c['text']
    assert all('395.3' not in ' '.join(c['heading_path']) for c in chunks if 'Passenger' in c['text'])

def test_numbered_password_rule_is_not_split_mid_item():
    chunks=chunk_pages(['3.1.1.2. Password Verifiers\nThe following rules apply to passwords.\n1. '+'a '*70+'\n7. Verifiers SHALL NOT permit a password hint accessible to an unauthenticated claimant.'],CONFIG)
    rule=next(c for c in chunks if 'hint' in c['text'])
    assert '7. Verifiers SHALL NOT permit' in rule['text']
    assert 'Password Verifiers' in ' '.join(rule['heading_path'])

def test_long_unstructured_text_is_bounded_and_has_no_invented_heading():
    chunks=chunk_pages(['word '*300],CONFIG)
    assert all(len(c['text'])<=250 and c['heading_path']==[] for c in chunks)

def test_inline_heading_separates_list_stem_from_previous_prose():
    value='# Accountable plans\n'+'Prior discussion. '*28+'\nReasonable period of time. The following list describes timing.\n• Account within 60 days.\n• Return excess within 120 days.'
    chunks=chunk_pages([value],{'chunking':'section','chunk_size':800,'chunk_overlap':120})
    target=next(c for c in chunks if '120 days' in c['text'])
    assert 'Reasonable period of time' in target['heading_path']
    assert 'following list' in target['text'] and '60 days' in target['text']

def test_short_regulatory_siblings_share_their_parent_context():
    chunks=chunk_pages(['§ 10.2 Driving limits.\n\n(a) Drivers must comply with these requirements:\n\n(1) Work window. The window is 14 hours.\n\n(2) Driving time. Drive at most 11 hours during that window.'],{'chunking':'section','chunk_size':800,'chunk_overlap':120})
    target=next(c for c in chunks if '11 hours' in c['text'])
    assert '14 hours' in target['text'] and 'Drivers must comply' in target['text']

def test_ambiguous_alphabetic_regulation_markers_do_not_inherit_sibling_scope():
    pages=['§ 10.1 Payments.\n(h) Contractor payments. '+'Contractor rule. '*25+'\n(i) Employee payments. '+'Employee rule. '*25]
    chunks=chunk_pages(pages,CONFIG)
    employee=next(c for c in chunks if 'Employee rule' in c['text'])
    assert not any('Contractor' in h for h in employee['heading_path'])
    nested=chunk_pages(['§ 10.1 Payments.\n(a) Payments.\n(1) Amounts.\n(i) First amount. '+'Amount rule. '*25],CONFIG)
    amount=next(c for c in nested if 'Amount rule' in c['text'])
    assert any('(1)' in h for h in amount['heading_path'])
    assert any('(i)' in h for h in amount['heading_path'])

def test_outer_alphabetic_sibling_after_numbered_children_drops_uncertain_scope():
    for previous,current,following in [('h','i','j'),('u','v','w'),('w','x','y')]:
        chunks=chunk_pages([f'§ 10.1 Payments.\n({previous}) Contractor payments.\n(1) Contractor timing. '+
                           'Contractor rule. '*25+f'\n({current}) Employee payments. '+'Employee rule. '*25+
                           f'\n({following}) Other payments.'],CONFIG)
        employee=next(c for c in chunks if 'Employee rule' in c['text'])
        assert not any('Contractor' in h for h in employee['heading_path'])
