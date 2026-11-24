from crm.core.state_home import state_home


def test_state_home_is_under_tmp_path_without_opt_in(tmp_path):
    assert state_home().is_relative_to(tmp_path)
