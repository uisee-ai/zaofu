# Single Freeform Plan Fixture

## Product

Create a JSON lines counter.

## Tasks

### Task A: Count valid JSON lines

Read newline-delimited text, parse valid JSON rows, count valid and invalid
rows, and keep invalid row numbers for diagnostics.

### Task B: Return compact report

Return a dictionary with `valid`, `invalid`, and `invalid_lines`. This task
depends on Task A.
