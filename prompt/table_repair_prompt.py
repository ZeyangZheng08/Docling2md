TABLE_REPAIR_PROMPT = """
Convert the table content in this image into a standard Markdown pipe table (use vertical bars `|` to separate columns), and follow these requirements:

1. If the table contains merged cells (horizontal or vertical), duplicate and fill the merged cell content into all corresponding cells so that each row has the same number of columns.
2. The table must include a header row, and you must include an aligned separator line under the header (for example: `| --- | --- | ... |`).
3. Do not add any extra content such as explanations, titles, or formatting notes — output only the raw Markdown table text.
"""
