from pathlib import Path
from x99toolkit import sessionlog
ROOT=Path(__file__).resolve().parents[1]

def test_session_starts_before_gui_and_cli_construction():
    text=(ROOT/'x99toolkit/gui.py').read_text()
    main=text[text.index('def run_gui()'):]
    assert main.index('start_session_log(')<main.index('Window()')
    text=(ROOT/'x99toolkit/cli.py').read_text()
    main=text[text.index('def main()'):]
    assert main.index('start_session_log(')<main.index('argparse.ArgumentParser(')

def test_session_log_location_and_lifecycle(monkeypatch,tmp_path):
    sessionlog.finish_session_log('reset');monkeypatch.setenv('X99_TOOLKIT_HOME',str(tmp_path))
    path=sessionlog.start_session_log('Gen2-v2.6.3')
    sessionlog.log('TEST','session record')
    sessionlog.finish_session_log('done')
    text=path.read_text()
    assert path.parent==tmp_path/'LOGS'
    assert 'session record' in text and 'Session ended: done' in text
