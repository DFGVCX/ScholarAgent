"""Verify PDF raster density, CSS geometry and the canvas budget in a live browser."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def measure(page):
    page.locator('#rrPages canvas[data-rendered="true"]').wait_for(timeout=60000)
    return page.locator('#rrPages canvas[data-rendered="true"]').evaluate('''canvas => {
        const rect = canvas.getBoundingClientRect();
        const sheet = canvas.parentElement.getBoundingClientRect();
        const layer = canvas.parentElement.querySelector('.reader-text-layer').getBoundingClientRect();
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let ink = 0;
        for (let i = 0; i < pixels.length; i += 64) if (pixels[i] < 220 && pixels[i+1] < 220 && pixels[i+2] < 220) ink++;
        return {width: canvas.width, height: canvas.height, cssWidth: rect.width, cssHeight: rect.height,
            sheetWidth: sheet.width, layerWidth: layer.width, layerLeft: layer.left, canvasLeft: rect.left,
            dpr: window.devicePixelRatio, ink};
    }''')


def verify(info):
    target = min(max(2, info['dpr']),
                 (16777216 / (info['cssWidth'] * info['cssHeight'])) ** 0.5,
                 8192 / info['cssWidth'], 8192 / info['cssHeight'])
    assert abs(info['width'] - info['cssWidth'] * target) < 2, info
    assert abs(info['height'] - info['cssHeight'] * target) < 2, info
    assert info['width'] * info['height'] <= 16777216, info
    assert max(info['width'], info['height']) <= 8192, info
    assert abs(info['sheetWidth'] - info['cssWidth']) < 1, info
    assert abs(info['layerWidth'] - info['cssWidth']) < 1, info
    assert abs(info['layerLeft'] - info['canvasLeft']) < 1, info
    assert info['ink'] > 500, 'PDF canvas is blank or incomplete'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--paper-id', required=True)
    parser.add_argument('--output', type=Path, default=Path('storage/runtime/reader-resolution'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        auth = playwright.request.new_context()
        response = auth.post(f'{args.base_url}/auth/login', data={
            'username': 'demo', 'password': 'demo123', 'tenant_id': 'tenant_demo',
        })
        assert response.ok
        profile = response.json()
        auth.dispose()
        for dpr, width, height in ((1, 1440, 900), (1.5, 1440, 900), (2, 1440, 900), (3, 390, 844)):
            context = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=dpr)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script(
                f"localStorage.setItem('scholar.session.v1', {json.dumps(json.dumps(profile))});"
                "localStorage.setItem('scholar.sidebar.collapsed.v1', '1');"
            )
            page.goto(f'{args.base_url}/app.html')
            page.locator('#appShell:not(.hidden)').wait_for()
            page.evaluate('state.selectedConversationId = null')
            page.evaluate('(id) => openReader(id)', args.paper_id)
            info = measure(page)
            verify(info)
            results.append({'mode': 'fit', **info})
            page.screenshot(path=str(args.output / f'dpr-{dpr}.png'))
            if dpr == 2:
                for _ in range(12):
                    page.locator('#rrZoomIn').click()
                page.wait_for_function("document.getElementById('rrZoom').textContent === '300%'")
                info = measure(page)
                verify(info)
                results.append({'mode': 'max-zoom', **info})
                # Device density must refresh the raster even at manual zoom.
                cdp = context.new_cdp_session(page)
                page.evaluate("document.querySelector('#rrPages canvas').dataset.beforeDensityChange = 'true'")
                cdp.send('Emulation.setDeviceMetricsOverride', {
                    'width': width + 1, 'height': height, 'deviceScaleFactor': 1.5, 'mobile': False,
                })
                page.wait_for_function("!document.querySelector('#rrPages canvas')?.dataset.beforeDensityChange")
                info = measure(page)
                verify(info)
                results.append({'mode': 'density-change', **info})
            print(f'PASS: DPR {dpr}, raster {info["width"]} x {info["height"]}', flush=True)
            context.close()
        browser.close()
    assert not errors, errors
    (args.output / 'measurements.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: sharp raster, nonblank canvas, text-layer alignment and bounded pixel allocation')


if __name__ == '__main__':
    main()
