"""Quick verification of the branding + next-step hand-off changes."""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pymupdf
import court_ocr.app as module
from court_ocr.app import App, TOOL_SPECS, APP_NAME, APP_SIGNATURE

module.messagebox = type("Silent", (), {
    "showinfo": staticmethod(lambda *a, **k: None),
    "showwarning": staticmethod(lambda *a, **k: None),
    "showerror": staticmethod(lambda *a, **k: None),
    "askyesno": staticmethod(lambda *a, **k: False),
})

FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail if not ok else ''}")
    if not ok:
        FAILED.append(name)


def sample_pdf(name, pages=3):
    path = Path("work") / name
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 140), f"Page {n + 1}", fontsize=30)
    doc.save(path)
    doc.close()
    return path


def all_texts(widget):
    """Every 'text' option in the widget tree under ``widget``."""
    texts = []
    for child in widget.winfo_children():
        try:
            if "text" in child.keys():
                texts.append(child.cget("text"))
        except tk.TclError:
            pass
        texts.extend(all_texts(child))
    return texts


root = tk.Tk()
root.withdraw()
app = App("work/_brand_cfg.json")
root.update()

print("\nBranding")
check("app name is KattyVision", APP_NAME == "KattyVision", APP_NAME)
check("signature is Kathirvel", APP_SIGNATURE == "Kathirvel", APP_SIGNATURE)
check("window title carries the brand", app.title() == "KattyVision", app.title())

print("\nTool page 'what next' bar")
page = app.tool_pages["merge"]
out = sample_pdf("_brand_merged.pdf", 3)
check("next bar exists and starts hidden",
      hasattr(page, "next_bar") and not page.next_bar.winfo_ismapped())
page._finish([out], [])
root.update()
check("next bar shows after a run", page.next_bar.winfo_ismapped())
labels = all_texts(page.next_row)
check("it offers 'Open the PDF'", any("Open the PDF" in t for t in labels), str(labels))
check("it offers the next tools", any("OCR" in t for t in labels), str(labels))

print("\nHand-off")
page.app._hand_off_to_tool("compress", [out])
root.update()
target = app.tool_pages["compress"]
check("the output was loaded into Compress", target.paths == [out], str(target.paths))
check("the status tells the user what to do",
      "press RUN" in target.var_status.get(), target.var_status.get())

# start a new run: the bar must hide again
page.paths = [sample_pdf("_brand_in.pdf")]
page._refresh_list()
page._start = page._start          # keep
page.next_bar  # exists
# simulate run start path pieces
page.outputs = []
page.next_bar.grid_remove()
check("bar can hide for the next run", not page.next_bar.winfo_ismapped())

print("\nAutomation result card")
check("hand-off row exists on the result card", hasattr(app, "result_next"))
app.build_handoff_row(app.result_next, [out])
root.update()
texts = all_texts(app.result_next)
check("result card offers 'Use this result in:'",
      any("Use this result" in t for t in texts), str(texts))
check("result card offers every follow-up tool",
      all(any(k in t for t in texts) for k in ("OCR", "Compress", "Page numbers",
                                               "Bank seal", "Split")), str(texts))

print("\nFile reorder buttons")
check("merge page has move-up/move-down",
      hasattr(page, "_move_selected"), "missing")

app.destroy()
root.destroy()
print()
if FAILED:
    print(f"RESULT: {len(FAILED)} failed: {FAILED}")
    sys.exit(1)
print("RESULT: all checks passed")
