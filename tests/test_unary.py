import pytest
from skill_rule_detector.rules.arity import normalize_rule, migrate_identity
from skill_rule_detector.rules.matching import pattern, rule_matrix
from skill_rule_detector.rules.mining import mine_horn_rules_entity, to_entity_facts
from skill_rule_detector.rules.signature import canonical_rule_signature
from skill_rule_detector.facts import augment_graph
from skill_rule_detector.training import safe_rule

def fixture():
    return {'skill_name':'fixture','nodes':[{'name':'a','type':'FILE'},{'name':'b','type':'FILE'}],
            'edges':[{'source':'a','target':'b','edge_type':'READ'}],
            'unary_facts':[{'predicate':'ML_NODE_LOCAL_GE_50','entity':'a'}]}

def test_migrate_cross_atom_equality():
    old={'rule_key':'old','body':[{'predicate':'ML_NODE_LOCAL_GE_50','arg1':'?x:FILE','arg2':'?y:FILE'}],
         'head':{'predicate':'READ','arg1':'?x:FILE','arg2':'?y:FILE'}}
    new=migrate_identity(old)
    assert 'arg2' not in new['body'][0]
    assert new['head']['arg1']==new['head']['arg2']
    assert rule_matrix([fixture()],[old,new]).nnz==0
    assert new['legacy_rule_key']=='old'
    assert canonical_rule_signature(old)==canonical_rule_signature(new)

def test_unary_fol_signature_and_types():
    r={'body':[{'predicate':'ML_NODE_LOCAL_GE_50','arg1':'?x:FILE'}],
       'head':{'predicate':'READ','arg1':'?x:FILE','arg2':'?y:FILE'}}
    fol='FILE(?x) ∧ FILE(?y) ∧ ML_NODE_LOCAL_GE_50(?x) ⇒ READ(?x,?y)'
    assert canonical_rule_signature(r)==canonical_rule_signature({'fol':fol})
    assert len(pattern(r)[0])==3
    assert rule_matrix([fixture()],[r]).nnz==1

def test_miner_serial_parallel_native_unary():
    graphs=[fixture(),{**fixture(),'skill_name':'fixture2'}]
    opts=dict(min_support=1,min_confidence=0,min_head_coverage=0,max_body_len=2,head_predicates=('READ',))
    a=mine_horn_rules_entity(graphs,workers=1,**opts)
    b=mine_horn_rules_entity(graphs,workers=2,**opts)
    keys_a = [canonical_rule_signature(r) for r in a['rules']]
    keys_b = [canonical_rule_signature(r) for r in b['rules']]
    assert keys_a == keys_b == sorted(keys_a)
    nodes=[atom for r in a['rules'] for atom in r['body'] if atom['predicate'].startswith('ML_NODE')]
    assert nodes and all('arg2' not in atom for atom in nodes)
    assert all(len(f)==2 for f in to_entity_facts(fixture())[0] if f[0].startswith('ML_NODE'))

def test_legacy_nonloop_fact_rejected():
    g=fixture(); g['edges'].append({'edge_type':'ML_NODE_LOCAL_GE_50','source':'a','target':'b'})
    with pytest.raises(ValueError,match='self-loop'):
        to_entity_facts(g)
