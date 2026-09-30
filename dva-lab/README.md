# dva-lab/

Reverse-engineering workbench for the Oracle Analytics `.dva` workbook format. It is where the format was decoded
before the generator in `liveinsight/platforms/oac/` was written. Kept for reference, not maintained.

- [`FORMATO-DVA.md`](FORMATO-DVA.md): the notes: the container, the catalog objects, the rules OAC enforces on
  import. Despite the file name, it is in English.
- `build_tests.py`: takes a workbook exported from OAC and produces modified `.dva` files in stages (a round trip:
  unpack, re-pack, then small changes), to find out which edits OAC accepts on import.
- `test6.py`: generates a new, renamed workbook from a specification, starting from a template workbook.
- `test7.py`: generates one workbook containing the 12 visual kinds of the "basic" library, rebinding their columns.

The scripts need sample `.dva` files exported from your own OAC instance. None is included here: they contain
instance-specific data and are not committed.
