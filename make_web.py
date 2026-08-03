#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# make_web.py
#
# Builds docs/index.html (the GitHub Pages web version of PIWISViewer)
# from web/template.html + web/engine.js + the dictionaries, report CSS,
# and report JS extracted from piwis_val_viewer.py. Run this after any
# change to the dictionaries or the renderer, then commit docs/index.html.
#
#   python make_web.py            build docs/index.html
#   python make_web.py --data F   also dump the data block to F as JSON
#                                 (used by the Node parity test harness)
# ---------------------------------------------------------------------------

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import piwis_val_viewer as pv


def build_data():
    return {
        "APP_NAME": pv.APP_NAME + " (web)",
        "CSS": pv.CSS,
        "JS": pv.JS,
        "TRANSLIT": {str(k): v for k, v in pv.TRANSLIT.items()},
        "PROTOCOL_TYPES": pv.PROTOCOL_TYPES,
        "MEAS_OBJECT_TITLES": pv.MEAS_OBJECT_TITLES,
        "TRANS_LABEL": pv.TRANS_LABEL,
        "TRANS_SEGMENT": pv.TRANS_SEGMENT,
        "TRANS_PHRASE": pv.TRANS_PHRASE,
        "TRANS_VALUE": pv.TRANS_VALUE,
        "TRANS_UNIT": pv.TRANS_UNIT,
        "ECU_GLOSS": pv.ECU_GLOSS,
        "EXTRA_STEMS": pv._EXTRA_STEMS,
        "STEM_EXCLUDE": sorted(pv._STEM_EXCLUDE),
    }


def data_json(data):
    # ensure_ascii keeps the page pure ASCII; escaping '</' prevents a
    # '</script>' inside string data from terminating the inline block
    return json.dumps(data, ensure_ascii=True).replace("</", "<\\/")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    data = build_data()

    if len(sys.argv) >= 3 and sys.argv[1] == "--data":
        with open(sys.argv[2], "w", encoding="ascii") as f:
            f.write(data_json(data))
        print("wrote %s" % sys.argv[2])
        return 0

    with open(os.path.join(here, "web", "template.html"),
              encoding="ascii") as f:
        template = f.read()
    with open(os.path.join(here, "web", "engine.js"),
              encoding="ascii") as f:
        engine = f.read()

    page = template.replace("/*__PDATA__*/",
                            "var PDATA = " + data_json(data) + ";")
    page = page.replace("/*__ENGINE__*/", engine)

    outdir = os.path.join(here, "docs")
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    out = os.path.join(outdir, "index.html")
    with open(out, "w", encoding="ascii", newline="\n") as f:
        f.write(page)
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
