"""Check profile geometry against a running local app without saving settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--output", type=Path, default=Path("storage/runtime/profile-layout"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    failures = []
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page()
        response = page.request.post(
            f"{args.base_url}/auth/login",
            data={"username": "demo", "password": "demo123", "tenant_id": "tenant_demo"},
        )
        assert response.ok, "Demo login failed"
        session = response.json()
        page.add_init_script(
            f"localStorage.setItem('scholar.session.v1', {json.dumps(json.dumps(session))});"
            "localStorage.setItem('scholar.sidebar.collapsed.v1', '1');"
        )
        page.goto(f"{args.base_url}/app.html")
        page.locator('#appShell:not(.hidden)').wait_for()
        page.locator('[data-route="profile"]').click()
        for width, height in ((1910, 910), (1366, 768), (1280, 720), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            for collapsed in (True, False):
                page.evaluate("value => applySidebarCollapsed(value)", collapsed)
                baseline = None
                for pane in ("settingsStorage", "settingsModel", "settingsRag", "settingsInstitution"):
                    page.locator(f'[data-settings-pane="{pane}"]').click()
                    page.wait_for_timeout(80)
                    geometry = page.evaluate("""() => {
                        const root = document.querySelector('#profileConfigMount');
                        const read = selector => {
                            const element = root.querySelector(selector);
                            const rect = element.getBoundingClientRect();
                            return {top: rect.top, bottom: rect.bottom, height: rect.height,
                                rows: getComputedStyle(element).gridTemplateRows};
                        };
                        return {grid: read('.settings-grid'), rail: read('.settings-sidebar'),
                            footer: read('.settings-bootstrap'), form: read('.settings-form-stack')};
                    }""")
                    results.append({"viewport": [width, height], "collapsed": collapsed,
                                    "pane": pane, **geometry})
                    if width > 1120:
                        label = f"{width}x{height} collapsed={collapsed} {pane}"
                        for name in ("rail", "footer", "form"):
                            if abs(geometry[name]["bottom"] - geometry["grid"]["bottom"]) > 1:
                                failures.append(f"{label}: {name} does not reach grid bottom")
                        if baseline is not None and abs(geometry['footer']['top'] - baseline) > 1:
                            failures.append(f"{label}: footer moves when switching panes")
                        baseline = geometry['footer']['top']
                    if collapsed and width in (1910, 390) and pane in ("settingsStorage", "settingsModel"):
                        page.screenshot(path=str(args.output / f"{width}-{pane}.png"), full_page=True)
        browser.close()
    (args.output / "geometry.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for failure in failures:
        print(failure)
    assert not failures, f"{len(failures)} profile layout checks failed"
    print(f"PASS: {len(results)} profile layouts; no settings were changed")


if __name__ == "__main__":
    main()
