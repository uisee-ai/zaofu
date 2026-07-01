def main(*args, **kwargs):
    from zf.cli.main import main as _main

    return _main(*args, **kwargs)

__all__ = ["main"]
