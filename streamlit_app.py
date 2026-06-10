"""
Bluebeam Markup Processor — Streamlit Web App
==============================================
Upload a PDF and Excel lookup file via browser.
Receive a processed PDF as a download.
No local Python installation needed for end users.

Run locally:
    streamlit run streamlit_app.py

Deploy:
    Push to GitHub and connect to Streamlit Community Cloud
    (see README_streamlit.txt for full instructions)
"""

import io
import re
import zlib
from datetime import datetime, timezone

import pandas as pd
import pikepdf
import streamlit as st


# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Bluebeam Markup Processor",
    page_icon="📄",
    layout="centered",
)


# ---------------------------------------------------------------------------
# CORE LOGIC  (file-path-free — works entirely on bytes / BytesIO)
# ---------------------------------------------------------------------------

def load_lookup_from_bytes(excel_bytes, sheet_name=None,
                           old_col="Old Value", new_col="New Value",
                           header_row=0):
    """
    Parse an Excel file from raw bytes into lookup_dict and delete_list.
    Returns (lookup_dict, delete_list, all_columns).
    """
    buf = io.BytesIO(excel_bytes)
    df = pd.read_excel(buf, sheet_name=sheet_name, header=header_row)
    all_columns = list(df.columns)

    if old_col not in df.columns or new_col not in df.columns:
        raise ValueError(
            f"Columns '{old_col}' and/or '{new_col}' not found.\n"
            f"Available: {all_columns}"
        )

    lookup_dict = {}
    delete_list = []

    for _, row in df.iterrows():
        if pd.isna(row[old_col]):
            continue
        raw = row[old_col]
        try:
            old_val = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
        except (ValueError, OverflowError):
            old_val = str(raw).strip()

        if pd.notna(row[new_col]):
            new_val = str(row[new_col]).strip()
            if new_val:
                lookup_dict[old_val] = new_val
            else:
                delete_list.append(old_val)
        else:
            delete_list.append(old_val)

    return lookup_dict, delete_list, all_columns


def get_excel_columns(excel_bytes, sheet_name=None, header_row=0):
    """Return just the column names from an Excel file (fast, no data loaded)."""
    buf = io.BytesIO(excel_bytes)
    df = pd.read_excel(buf, sheet_name=sheet_name, header=header_row, nrows=0)
    return list(df.columns)


def update_raw_field(raw_hex, old_value, new_value):
    """Update the zlib-compressed Bluebeam Raw annotation blob."""
    try:
        text = zlib.decompress(bytes.fromhex(raw_hex)).decode("latin-1")
        text = re.sub(
            r"/Contents\(" + re.escape(old_value) + r"\)",
            "/Contents(" + new_value + ")",
            text,
        )
        text = re.sub(
            r"<p([^>]*)>" + re.escape(old_value) + r"</p>",
            r"<p\1>" + new_value + "</p>",
            text,
        )
        text = re.sub(
            r"<span([^>]*)>" + re.escape(old_value) + r"</span>",
            r"<span\1>" + new_value + "</span>",
            text,
        )
        return zlib.compress(text.encode("latin-1")).hex()
    except Exception:
        return raw_hex


def _apply_update(annot, old_value, new_value, now_str, stats):
    """
    Apply a replacement to a single annotation object in-place.

    Updates all locations where Bluebeam stores visible text:
      /Contents  — the PDF-standard text field (used by Comments panel)
      /RC        — rich-content XML (used by some annotation subtypes)
      /Raw       — Bluebeam's private compressed blob
      /AP        — pre-rendered appearance stream; DELETED so that Revu
                   regenerates it from /Contents on next open. Without
                   this step the old text remains visible even though
                   /Contents has been updated.
    """
    # 1. Update /Contents
    annot["/Contents"] = pikepdf.String(new_value)

    # 2. Update /M (modification date)
    if "/M" in annot.keys():
        annot["/M"] = pikepdf.String(now_str)

    # 3. Update /RC rich-content XML — replace bare text occurrences
    rc = annot.get("/RC")
    if rc is not None:
        rc_str = str(rc)
        rc_str = re.sub(re.escape(old_value), new_value, rc_str)
        annot["/RC"] = pikepdf.String(rc_str)

    # 4. Update /Raw (Bluebeam private compressed blob)
    raw_key = "/Raw" if "/Raw" in annot.keys() else ("/BB:Raw" if "/BB:Raw" in annot.keys() else None)
    if raw_key:
        raw_hex = str(annot.get(raw_key) or "")
        if raw_hex:
            new_raw = update_raw_field(raw_hex, old_value, new_value)
            annot[raw_key] = pikepdf.String(new_raw)
            stats["raw_updated"] += 1

    # 5. Delete /AP appearance stream — forces Revu to regenerate the
    #    visible text from /Contents on next open. This is the critical
    #    step: without it Revu displays the cached pre-rendered appearance
    #    and ignores the updated /Contents value entirely.
    if "/AP" in annot.keys():
        del annot["/AP"]


def process_pdf_bytes(pdf_bytes, lookup_dict, delete_list,
                      delete_empty=False, delete_notfound=False):
    """
    Process PDF from raw bytes. Returns (output_bytes, stats).
    All work is done in memory — no temp files written to disk.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    stats = {
        "total": 0, "modified": 0, "deleted": 0, "skipped": 0,
        "raw_updated": 0, "changes": [], "deletions": [],
    }

    pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes))

    for page_idx, page in enumerate(pdf.pages):
        page_label = f"Page {page_idx + 1}"
        annots = page.get("/Annots")
        if annots is None:
            continue

        keep = []
        for annot in list(annots):
            stats["total"] += 1
            contents_obj = annot.get("/Contents")

            if contents_obj is None:
                keep.append(annot)
                stats["skipped"] += 1
                continue

            old_value = str(contents_obj).strip()
            if not old_value:
                keep.append(annot)
                stats["skipped"] += 1
                continue

            if delete_empty and old_value in delete_list:
                stats["deleted"] += 1
                stats["deletions"].append({
                    "page": page_label, "value": old_value,
                    "reason": "Blank replacement value",
                })

            elif old_value in lookup_dict:
                new_value = lookup_dict[old_value]
                _apply_update(annot, old_value, new_value, now_str, stats)
                stats["modified"] += 1
                stats["changes"].append({
                    "page": page_label, "old": old_value, "new": new_value,
                })
                keep.append(annot)

            elif delete_notfound and old_value not in delete_list:
                stats["deleted"] += 1
                stats["deletions"].append({
                    "page": page_label, "value": old_value,
                    "reason": "Not in lookup table",
                })

            else:
                keep.append(annot)

        page["/Annots"] = pikepdf.Array(keep)

    # Tell any PDF viewer (including Revu) to regenerate appearance streams
    # for all annotations whose /AP was removed. Without this flag some
    # viewers may show blank boxes instead of regenerating from /Contents.
    if "/AcroForm" in pdf.Root.keys():
        pdf.Root.AcroForm["/NeedAppearances"] = True
    else:
        pdf.Root.AcroForm = pikepdf.Dictionary(
            Fields=pikepdf.Array(),
            NeedAppearances=True,
        )

    out_buf = io.BytesIO()
    pdf.save(out_buf)
    pdf.close()
    return out_buf.getvalue(), stats


# ---------------------------------------------------------------------------
# SESSION STATE HELPERS
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "excel_columns": [],
        "result_bytes": None,
        "result_filename": None,
        "stats": None,
        "error": None,
        "processing": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_init_state()

st.title("📄 Bluebeam Markup Processor")
st.caption(
    "Upload a Bluebeam PDF and an Excel lookup table. "
    "Markup Contents values are matched against the lookup and either "
    "replaced or deleted. Download the processed PDF when done."
)
st.divider()

# ── Step 1: File uploads ───────────────────────────────────────────────────
st.subheader("1 · Upload files")
col_pdf, col_xl = st.columns(2)

with col_pdf:
    pdf_file = st.file_uploader(
        "PDF file", type=["pdf"],
        help="The Bluebeam PDF whose markups you want to process.",
    )

with col_xl:
    excel_file = st.file_uploader(
        "Excel lookup table", type=["xlsx", "xls"],
        help="Spreadsheet with your search and replacement values.",
    )

# ── Step 2: Excel settings ─────────────────────────────────────────────────
st.subheader("2 · Excel settings")

xl_col1, xl_col2, xl_col3 = st.columns([2, 1, 1])

with xl_col1:
    sheet_name = st.text_input(
        "Sheet name",
        value="",
        placeholder="Leave blank for first sheet",
        help="Name of the Excel sheet to read. Leave blank to use the first sheet.",
    )

with xl_col2:
    header_row = st.number_input(
        "Header row", min_value=1, max_value=20, value=1, step=1,
        help="Row number (1-based) that contains column headers.",
    )

with xl_col3:
    st.write("")  # spacer
    st.write("")
    load_cols_btn = st.button("🔄 Load columns", use_container_width=True,
                               disabled=(excel_file is None))

# Load columns when button clicked or when excel is freshly uploaded
if load_cols_btn and excel_file is not None:
    try:
        excel_file.seek(0)
        cols = get_excel_columns(
            excel_file.read(),
            sheet_name=sheet_name.strip() or None,
            header_row=int(header_row) - 1,
        )
        st.session_state.excel_columns = cols
        st.session_state.error = None
    except Exception as e:
        st.session_state.error = f"Could not read Excel columns: {e}"
        st.session_state.excel_columns = []

# Auto-load columns on fresh upload
if excel_file is not None and not st.session_state.excel_columns:
    try:
        excel_file.seek(0)
        cols = get_excel_columns(
            excel_file.read(),
            sheet_name=sheet_name.strip() or None,
            header_row=int(header_row) - 1,
        )
        st.session_state.excel_columns = cols
    except Exception:
        pass

# Column selectors
cols_available = st.session_state.excel_columns
use_dropdowns = len(cols_available) > 0

col_s, col_r = st.columns(2)
with col_s:
    if use_dropdowns:
        # Default to first column for search
        default_old = cols_available[0] if cols_available else None
        old_col = st.selectbox(
            "Search column \\*",
            options=cols_available,
            index=0,
            help="Column whose values are matched against markup Contents.",
        )
    else:
        old_col = st.text_input(
            "Search column \\*",
            value="Old Value",
            help="Column whose values are matched against markup Contents.",
        )

with col_r:
    if use_dropdowns:
        default_new = cols_available[1] if len(cols_available) > 1 else cols_available[0]
        default_new_idx = 1 if len(cols_available) > 1 else 0
        new_col = st.selectbox(
            "Replace column \\*",
            options=cols_available,
            index=default_new_idx,
            help="Column whose values replace the matched markup Contents.",
        )
    else:
        new_col = st.text_input(
            "Replace column \\*",
            value="New Value",
            help="Column whose values replace the matched markup Contents.",
        )

if cols_available:
    st.caption(f"Columns detected: {', '.join(str(c) for c in cols_available)}")

# ── Step 3: Options ────────────────────────────────────────────────────────
st.subheader("3 · Options")

opt1, opt2 = st.columns(2)
with opt1:
    delete_empty = st.checkbox(
        "Delete markups with blank replacement",
        value=False,
        help="If a markup's value is found in the search column but the "
             "replacement cell is empty, delete the markup.",
    )
with opt2:
    delete_notfound = st.checkbox(
        "Delete markups not in lookup table",
        value=False,
        help="If a markup's value doesn't appear in the search column at all, "
             "delete it.",
    )

# ── Step 4: Run ────────────────────────────────────────────────────────────
st.subheader("4 · Process")

if st.session_state.error:
    st.error(st.session_state.error)

run_disabled = (pdf_file is None or excel_file is None or not old_col or not new_col)

if st.button(
    "▶ Run processor",
    type="primary",
    use_container_width=True,
    disabled=run_disabled,
):
    st.session_state.result_bytes = None
    st.session_state.stats = None
    st.session_state.error = None

    with st.spinner("Processing…"):
        try:
            # Read uploaded file bytes
            pdf_file.seek(0)
            pdf_bytes = pdf_file.read()

            excel_file.seek(0)
            excel_bytes = excel_file.read()

            # Load lookup table
            lookup_dict, delete_list, _ = load_lookup_from_bytes(
                excel_bytes,
                sheet_name=sheet_name.strip() or None,
                old_col=old_col,
                new_col=new_col,
                header_row=int(header_row) - 1,
            )

            # Process
            out_bytes, stats = process_pdf_bytes(
                pdf_bytes, lookup_dict, delete_list,
                delete_empty=delete_empty,
                delete_notfound=delete_notfound,
            )

            # Build output filename
            original_name = getattr(pdf_file, "name", "output.pdf")
            stem = original_name.rsplit(".", 1)[0]
            result_name = f"{stem}_processed.pdf"

            st.session_state.result_bytes = out_bytes
            st.session_state.result_filename = result_name
            st.session_state.stats = stats

        except Exception as e:
            st.session_state.error = str(e)
            st.error(f"❌ {e}")

# ── Step 5: Results + download ─────────────────────────────────────────────
if st.session_state.stats is not None:
    stats = st.session_state.stats

    st.divider()
    st.subheader("5 · Results")

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total annotations", stats["total"])
    m2.metric("Modified", stats["modified"], delta=None)
    m3.metric("Deleted", stats["deleted"], delta=None)
    m4.metric("Skipped", stats["skipped"])

    # Download button
    st.download_button(
        label="⬇ Download processed PDF",
        data=st.session_state.result_bytes,
        file_name=st.session_state.result_filename,
        mime="application/pdf",
        type="primary",
        use_container_width=True,
    )

    # Detail tables
    if stats["changes"]:
        with st.expander(f"✅ Changes made ({len(stats['changes'])})"):
            st.dataframe(
                pd.DataFrame(stats["changes"]).rename(columns={
                    "page": "Page", "old": "Original value", "new": "New value"
                }),
                use_container_width=True,
                hide_index=True,
            )

    if stats["deletions"]:
        with st.expander(f"🗑 Deletions ({len(stats['deletions'])})"):
            st.dataframe(
                pd.DataFrame(stats["deletions"]).rename(columns={
                    "page": "Page", "value": "Value", "reason": "Reason"
                }),
                use_container_width=True,
                hide_index=True,
            )

    if not stats["changes"] and not stats["deletions"]:
        st.info("No changes were made — no matching values found in the lookup table.")

# ── Footer ─────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Files are processed entirely in memory and never stored on the server. "
    "Uploaded files are discarded as soon as processing completes."
)
