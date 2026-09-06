"""Verify metadata layout and editing without changing stored documents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--paper-id', required=True)
    parser.add_argument('--output', type=Path, default=Path('storage/runtime/paper-metadata-ui'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        login = page.request.post(f'{args.base_url}/auth/login', data={
            'username': 'demo', 'password': 'demo123', 'tenant_id': 'tenant_demo',
        })
        assert login.ok
        page.add_init_script(
            f"localStorage.setItem('scholar.session.v1', {json.dumps(json.dumps(login.json()))});"
            "localStorage.setItem('scholar.sidebar.collapsed.v1', '1');"
        )
        page.goto(f'{args.base_url}/app.html')
        page.locator('#appShell:not(.hidden)').wait_for()
        page.evaluate('state.selectedConversationId = null')
        page.locator('[data-route="knowledge"]').click()
        page.wait_for_function('(id) => state.knowledge.some(item => item.paper_id === id)', arg=args.paper_id)
        page.evaluate('''id => {
            state.selectedPaperId = id;
            state.paperViewMode = 'metadata';
            renderKnowledge(); renderPaperWorkbench();
        }''', args.paper_id)
        title = page.locator('#bibliography-title').input_value()
        assert page.locator('#paperViewer input[data-bibliography-field], #paperViewer textarea[data-bibliography-field]').count() == 9
        assert page.locator('#paperViewer [data-bibliography-link]').count() == 4
        assert page.locator('#pageKnowledge .reader-tools').is_hidden()
        assert page.locator('#savePaperMetadataBtn').is_disabled()
        assert page.locator('.paper-metadata-evidence').is_hidden()
        for width, height in ((1910, 910), (1440, 900), (1280, 720), (390, 844)):
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_timeout(150)
            page.locator('.paper-metadata-body').evaluate('(el) => el.scrollTop = 0')
            info = page.locator('.paper-metadata-editor').evaluate('''el => {
                const body = el.querySelector('.paper-metadata-body');
                const footer = el.querySelector('.paper-metadata-actions');
                return {width:el.clientWidth, contentWidth:el.scrollWidth,
                    bodyBottom:body.getBoundingClientRect().bottom,
                    footerTop:footer.getBoundingClientRect().top,
                    footerBottom:footer.getBoundingClientRect().bottom,
                    editorBottom:el.getBoundingClientRect().bottom};
            }''')
            assert info['contentWidth'] <= info['width'] + 1, info
            assert info['bodyBottom'] <= info['footerTop'] + 1, info
            assert info['footerBottom'] <= info['editorBottom'] + 1, info
            if width >= 1120:
                assert info['footerBottom'] <= height, info
            page.screenshot(path=str(args.output / f'metadata-{width}.png'))
            print(f'PASS: metadata geometry {width} x {height}: {info}', flush=True)

        page.set_viewport_size({'width': 1440, 'height': 900})
        page.locator('#bibliography-title').fill('Metadata edit regression')
        page.locator('#bibliography-authors').fill('Author One\nAuthor Two')
        page.locator('#bibliography-doi').fill('10.0000/ui-fixture')
        assert page.locator('#savePaperMetadataBtn').is_enabled()
        page.locator('[data-bibliography-field="links"] > summary').click()
        page.locator('#bibliography-link-code').fill('https://example.org/code')
        payloads = []

        def reject_save(route):
            payloads.append(route.request.post_data_json)
            route.fulfill(status=503, json={'detail': 'Test-only save failure'})

        route_pattern = f'**/knowledge/{quote(args.paper_id, safe="")}/metadata'
        page.route(route_pattern, reject_save)
        page.locator('#savePaperMetadataBtn').click()
        page.wait_for_function("document.body.textContent.includes('Test-only save failure')")
        assert len(payloads) == 1
        assert payloads[0]['authors'] == ['Author One', 'Author Two']
        assert payloads[0]['links']['code'] == ['https://example.org/code']
        assert page.locator('#bibliography-title').input_value() == 'Metadata edit regression'
        page.locator('#resetPaperMetadataBtn').click()
        assert page.locator('#bibliography-title').input_value() == title
        assert page.locator('#savePaperMetadataBtn').is_disabled()
        page.unroute(route_pattern)
        page.evaluate("setPreviewMode('text')")
        assert page.locator('#pageKnowledge .reader-tools').is_visible()
        assert page.locator('#paperViewer.metadata-view').count() == 0
        assert not errors, errors
        browser.close()
    print('PASS: editable fields, safe save failure, restore and isolated view switching')


if __name__ == '__main__':
    main()
