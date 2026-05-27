# Building ID Cards from the CLOCKIN Roster Export

This guide covers the workflow for printing student ID cards on the MagiCard
600, using CLOCKIN as the master roster.

## The workflow

```
1. Maintain your roster in CLOCKIN (import students, assign roles, etc.)
2. CLOCKIN -> Roster page -> "EXPORT ROSTER" -> downloads roster_export_*.csv
3. Open that CSV in Excel or Google Sheets
4. Add ONE formula column: qr_data  (instructions below)
5. Save as CSV
6. In MagiCard's software: use its database/batch feature to read the CSV
   - map the photo column to the photo field
   - map qr_data to the QR field
   - map the text columns to the text fields
7. Batch print
```

CLOCKIN is the master roster — it owns the Employee IDs. The spreadsheet is
just a working file between CLOCKIN and the card printer.

## What the export gives you

`roster_export_YYYY-MM-DD.csv` has these columns:

`employee_id, name, first_name, last_name, student_id, school_year, school,
role, course, period, active`

For ID cards you mainly use: `name`, `student_id` (the school ID #),
`school_year`, `employee_id`, and `school`.

## Step 1 — add the photo column

CLOCKIN does not store student photos. Add a column called `photo` yourself,
containing each student's photo filename, e.g. `128431.jpg`. Name the photo
files to match — using the `student_id` is a good scheme. Put all the photos
in one folder that MagiCard's software can reach.

## Step 2 — add the qr_data column (the formula)

The back of the card has a QR code. MagiCard's QR field turns a text column
into a QR automatically — so you just need a column containing the right
text. That text is the same JSON CLOCKIN's own badges use.

Add a column named `qr_data`. In the first data row (row 2), paste this
formula. It assumes the export's default column order, where:

- column A = employee_id
- column B = name
- column E = student_id
- column G = school

**Excel / Google Sheets formula:**

```
="{""school"":"""&G2&""",""name"":"""&B2&""",""employee_id"":"""&A2&""",""student_id"":"""&E2&"""}"
```

Then drag it down so every row gets its own `qr_data`. Each cell produces
something like:

```
{"school":"CTEC","name":"Alex Rivera","employee_id":"ITF-001","student_id":"128431"}
```

If your columns are in a different order, adjust the letters (G2, B2, A2, E2)
to point at the right cells. The four pieces, in order, are school, name,
employee_id, student_id.

### Why the formula looks messy

Spreadsheets use doubled quotes (`""`) to put a literal quote mark inside a
text formula. The JSON needs real quote marks around each value, hence all
the `""`. Write it once, drag down, done.

## Step 3 — save and import into MagiCard

Save the sheet as CSV. In MagiCard's software, use its database / batch
import feature, point it at the CSV, and map:

- `photo` -> the photo image field
- `qr_data` -> the QR code field
- `name`, `student_id`, `school_year` -> the matching text fields on the front
- `name`, `employee_id` -> the text fields on the back
- `period` -> the A.M./P.M. shift indicator

Then batch print.

## A note on the shift (A.M./P.M.)

The card back shows A.M. or P.M. The export's `period` column already holds
exactly `A.M.` or `P.M.`, so map that column straight to the shift field.

## Keeping things in sync

Because CLOCKIN is the master, the routine is always: change the roster in
CLOCKIN first, then re-export. Don't hand-edit the working spreadsheet's
student data — if you do, it won't match CLOCKIN. The spreadsheet only adds
the two columns CLOCKIN can't provide: `photo` and `qr_data`.

---

*CTEC — Dragon Technologies classroom operations suite. — Ciri*
