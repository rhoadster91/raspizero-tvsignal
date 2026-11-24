from pathlib import Path

import pytest

from tvsignal import cli, config
from tvsignal.config import ConfigError


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_defaults_when_default_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "absent.toml")
    cfg = config.load(None)
    assert cfg == config.Config()
    assert (cfg.timing.interval, cfg.timing.on_delay, cfg.timing.off_delay, cfg.timing.heartbeat) == (2, 0, 10, 600)
    assert cfg.sink.type == "log"


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        config.load(tmp_path / "absent.toml")


def test_example_config_parses():
    cfg = config.validate(config.load(Path(__file__).parent.parent / "deploy" / "config.example.toml"))
    assert cfg.tv.credentials == Path("/var/lib/tvsignal/credentials.json")
    assert cfg.sink.type == "log"


def test_parse_and_overrides(tmp_path):
    p = write(tmp_path, """
[tv]
host = "192.0.2.10"
[sink]
type = "shelly"
host = "192.0.2.20"
password = "file"
[timing]
off_delay = 30
""")
    cfg = config.load(p)
    assert cfg.timing.off_delay == 30 and cfg.timing.interval == 2
    cfg = config.apply_overrides(cfg, sink_host="192.0.2.21", off_delay=5, env={})
    assert cfg.sink.host == "192.0.2.21" and cfg.timing.off_delay == 5 and cfg.sink.password == "file"
    cfg = config.apply_overrides(cfg, env={config.PASSWORD_ENV: "env"})
    assert cfg.sink.password == "env"
    assert "env" not in cfg.describe() and "(auth)" in cfg.describe()


@pytest.mark.parametrize("text, match", [
    ("[tv]\nhots = 'x'\n", "unknown key tv.hots"),
    ("[nope]\n", "unknown section"),
    ("[timing]\ninterval = 'fast'\n", "must be a number"),
    ("[sink]\ntype = 'zigbee'\n", "sink.type must be"),
    ("[sink]\ntype = 'shelly'\n", "needs sink.host"),
    ("[timing]\ninterval = 0\n", "interval must be > 0"),
    ("[timing]\noff_delay = -1\n", ">= 0"),
    ("[tv\n", "c.toml"),
])
def test_invalid(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        config.validate(config.load(write(tmp_path, text)))


def test_cli_flags_reach_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.PASSWORD_ENV, raising=False)
    p = write(tmp_path, "[timing]\ninterval = 3\n")
    args = cli.build_parser().parse_args(
        ["run", "--config", str(p), "--sink", "shelly", "--sink-host", "192.0.2.20", "--on-delay", "1"])
    cfg = cli.load_config(args)
    assert (cfg.sink.type, cfg.sink.host, cfg.timing.interval, cfg.timing.on_delay) == ("shelly", "192.0.2.20", 3, 1)


def test_cli_config_error_exit_code(tmp_path):
    assert cli.main(["check", "--config", str(tmp_path / "absent.toml")]) == cli.EXIT_CONFIG


def test_cli_missing_credentials_exit_code(tmp_path):
    p = write(tmp_path, f"[tv]\ncredentials = '{tmp_path / 'none.json'}'\n")
    assert cli.main(["run", "--config", str(p)]) == cli.EXIT_AUTH


def test_cli_sink_status_log_only(tmp_path, capsys):
    p = write(tmp_path, "")
    assert cli.main(["sink", "status", "--config", str(p)]) == 0
    assert "no physical plug" in capsys.readouterr().out
    assert cli.main(["sink", "on", "--config", str(p)]) == cli.EXIT_CONFIG
