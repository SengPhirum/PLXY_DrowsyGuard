from drowsyguard.data import subject_split

def test_subjects_do_not_overlap():
    s=subject_split([f's{i}' for i in range(10)], seed=1)
    sets=[set(s[k]) for k in ('train','val','test')]
    assert not (sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2])
    assert set().union(*sets)=={f's{i}' for i in range(10)}
