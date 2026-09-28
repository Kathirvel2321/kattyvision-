"""Screenshot the rebuilt UI so the layout can be eyeballed."""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from court_ocr.app import App  # noqa: E402
import court_ocr.app as module  # noqa: E402


class Silent:
    @staticmethod
    def showinfo(*_a, **_k):
        pass

    @staticmethod
    def showwarning(*_a, **_k):
        pass

    @staticmethod
    def showerror(*_a, **_k):
        pass

    @staticmethod
    def askyesno(*_a, **_k):
        return False


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "work/ui_shot.png")
    mode = sys.argv[2] if len(sys.argv) > 2 else "main"
    target.parent.mkdir(parents=True, exist_ok=True)
    module.messagebox = Silent
    module.open_in_explorer = lambda *_a, **_k: None

    app = App(Path("work/_shot_cfg.json"))
    app.var_input.set(str(Path("input/SAMPLE CASE")))
    app.var_case.set("SA 245 of 2026")
    app._check_documents()
    app.update_idletasks()
    app.update()

    if mode == "seal":
        import pymupdf
        gen = Path("work") / "_shot_seal.pdf"
        if not gen.is_file():
            doc = pymupdf.open()
            for n in range(6):
                sheet = doc.new_page(width=595, height=842)
                sheet.insert_text((72, 120), "Borrower: SA 245 of 2026", fontsize=22)
                sheet.insert_text((72, 165), f"Exhibit page {n + 1}", fontsize=28)
            doc.save(gen)
            doc.close()
        app.book.select(1)
        app.tool_book.select([s.key for s in module.TOOL_SPECS].index("stamp"))
        app.update_idletasks()
        app.update()
        page = app.tool_pages["stamp"]
        out = Path("work") / "shot_out"
        out.mkdir(parents=True, exist_ok=True)
        page.paths = [gen]
        page.var_out.set(str(out))
        page.option_vars["seal_image"].set(str(Path("assets/stamp.png")))
        page._refresh_list()
        app.update()
        page.scroller.scroll_to(0.22)
        app.update()
    elif mode == "split":

        gen = Path("work") / "_shot_split.pdf"
        if not gen.is_file():
            doc = pymupdf.open()
            for n in range(20):
                page = doc.new_page(width=595, height=842)
                page.insert_text((72, 140), f"Exhibit page {n + 1}", fontsize=30)
            doc.save(gen)
            doc.close()
        gen2 = Path("work") / "_shot_split2.pdf"
        if not gen2.is_file():
            doc = pymupdf.open()
            for n in range(5):
                sheet = doc.new_page(width=595, height=842)
                sheet.insert_text((72, 140), f"Annexure {n + 1}", fontsize=30)
            doc.save(gen2)
            doc.close()
        app.book.select(1)
        # TOOL_SPECS order is merge, ocr, compress, paginate, stamp, split -
        # select it by position, because matching on the tab label proved
        # unreliable (notebook tab text comes back with padding).
        split_index = [s.key for s in module.TOOL_SPECS].index("split")
        app.tool_book.select(split_index)
        app.update_idletasks()
        app.update()
        page = app.tool_pages["split"]
        out = Path("work") / "shot_out"
        out.mkdir(parents=True, exist_ok=True)
        page.paths = [gen, gen2]
        page.var_out.set(str(out))
        page._refresh_list()
        app.update()
        # Scroll so the page map itself is what the shot shows.
        page.scroller.scroll_to(0.5)
        app.update()
        pm = page.page_map
        deadline = time.time() + 40
        while pm._queue and time.time() < deadline:
            app.update()
            time.sleep(0.02)
        pm._toggle(pm.tiles[4].page_ref)
        pm._toggle(pm.tiles[11].page_ref)
        page.scroller.scroll_to(0.34)
        app.update()
    elif mode == "advanced":
        app._open_advanced()
    elif mode == "settings":
        app.book.select(2)

    app.update_idletasks()
    app.update()
    app.after(600, app.quit)
    app.mainloop()
    app.update()

    app.attributes("-topmost", True)
    app.deiconify()
    app.lift()
    app.focus_force()
    app.update_idletasks()
    app.update()
    try:
        from PIL import ImageGrab

        shotter = app
        if mode == "advanced":
            for child in app.winfo_children():
                if child.winfo_class() == "Toplevel" and child.winfo_viewable():
                    shotter = child
                    break
        shotter.attributes("-topmost", True)
        shotter.deiconify()
        shotter.lift()
        shotter.focus_force()
        shotter.update_idletasks()
        shotter.update()
        x, y = shotter.winfo_rootx(), shotter.winfo_rooty()
        w, h = shotter.winfo_width(), shotter.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(target)
        print(f"saved {target}  ({w}x{h})")
    except Exception as exc:
        print("screenshot unavailable:", exc)
    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
