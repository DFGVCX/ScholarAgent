"""Read-only RAG settings and PDF reader checks against a running demo instance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

import fitz
from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--paper-id', required=True)
    parser.add_argument('--output', type=Path, default=Path('storage/runtime/workspace-ui'))
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
            "localStorage.setItem('scholar.sidebar.collapsed.v1','1');"
        )
        page.goto(f'{args.base_url}/app.html')
        page.locator('#appShell:not(.hidden)').wait_for()
        page.evaluate('state.selectedConversationId = null')
        page.locator('[data-route="profile"]').click()
        page.locator('[data-settings-pane="settingsRag"]').click()
        page.wait_for_function("document.getElementById('saveRuntimeConfigBtn').disabled === false")
        original_model = page.locator('#cfgRagEmbeddingModel').input_value()
        for width, height in ((1440, 900), (1280, 720), (390, 844)):
            page.set_viewport_size({'width': width, 'height': height})
            for tab in ('Embedding', 'Documents', 'Retrieval', 'Index', 'Search'):
                page.locator(f'#ragTab{tab}').click()
                assert page.locator('#settingsRag [role="tabpanel"]:visible').count() == 1
                assert page.locator(f'#ragTab{tab}').get_attribute('aria-selected') == 'true'
                if width > 1120:
                    assert page.locator('#saveRuntimeConfigBtn').bounding_box()['y'] < height
                page.screenshot(path=str(args.output / f'rag-{width}-{tab}.png'))
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.locator('#ragTabEmbedding').click()
        page.locator('#cfgRagEmbeddingModel').fill('unsaved-layout-check')
        page.locator('#ragTabDocuments').click()
        page.locator('#ragTabEmbedding').click()
        assert page.locator('#cfgRagEmbeddingModel').input_value() == 'unsaved-layout-check'
        page.locator('#cfgRagEmbeddingModel').fill(original_model)
        page.locator('#ragTabEmbedding').press('ArrowRight')
        assert page.locator('#ragTabDocuments').get_attribute('aria-selected') == 'true'
        keys = page.locator('#settingsRag [data-config-key]').evaluate_all('(els) => els.map(e => e.dataset.configKey)')
        assert len(keys) == len(set(keys)) == 21
        print('PASS: RAG tab switching, keyboard navigation, 21 settings and unsaved input retention')

        fixture = {'backend': 'pgvector', 'retrieval_mode': 'lexical', 'items': [{
            'title': 'Layout fixture <not-html>', 'paper_id': 'fixture', 'chunk_id': 'chunk-fixture',
            'snippet': 'A test-only retrieval result.', 'chunk_index': 1, 'score': 0.75,
            'page_start': 2, 'section_path': 'Methods', 'lexical_rank': 1,
        }]}
        page.route('**/knowledge/rag/search?**', lambda route: route.fulfill(json=fixture))
        page.locator('#ragTabSearch').click()
        page.locator('#profileRagQuery').fill('layout fixture')
        page.locator('#profileRagSearchBtn').click()
        page.locator('#profileRagResult .rag-result-item').wait_for()
        assert '<not-html>' in page.locator('#profileRagResult h4').inner_text()
        assert page.locator('#profileRagResult not-html').count() == 0
        page.unroute('**/knowledge/rag/search?**')

        page.evaluate('(id) => openReader(id)', args.paper_id)
        page.locator('#rrPages canvas').wait_for(timeout=60000)
        page.wait_for_function("document.querySelectorAll('#rrPages .reader-text-layer span').length > 0")
        page.wait_for_timeout(400)
        pixels = page.locator('#rrPages canvas').evaluate('''canvas => {
            const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            let ink = 0;
            for (let i = 0; i < data.length; i += 4) if (data[i] < 220 && data[i+1] < 220 && data[i+2] < 220 && data[i+3]) ink++;
            return ink;
        }''')
        assert pixels > 1000, 'PDF canvas is blank'
        def document_width():
            return page.locator('.reader-document').bounding_box()['width']
        normal = document_width()
        assert normal > page.locator('#rrBody').bounding_box()['width'] * 0.65
        page.locator('#rrToggleLeft').click()
        assert document_width() < normal - 150
        assert page.locator('#rrNavSide').evaluate('(node) => !node.inert')
        page.locator('#rrToggleLeft').click()
        page.locator('#rrToggleRight').click()
        assert document_width() > normal + 200
        page.locator('#rrToggleRight').click()
        page.locator('#rrSearch').click()
        assert page.locator('#rrFindInput').is_visible()
        page.wait_for_function("document.querySelectorAll('#rrPages .reader-text-layer span').length > 0")
        term = page.locator('.reader-text-layer span').evaluate_all('(nodes) => nodes.map(n => n.textContent.trim()).find(s => s.length > 0).slice(0,3)')
        page.locator('#rrFindInput').fill(term)
        page.wait_for_function("document.getElementById('rrFindStatus').textContent.match(/^1 \\/ \\d+$/)")
        assert page.locator('.search-current').count() > 0
        page.locator('#rrFindInput').fill('nonexistent-layout-regression-token-4931')
        page.wait_for_function("document.getElementById('rrFindStatus').textContent === '\u65e0\u5339\u914d'")
        page.locator('#rrFindClose').click()
        page.screenshot(path=str(args.output / 'reader-desktop.png'))

        pdf = fitz.open()
        pdf.new_page().insert_text((72, 72), 'First page shared-term')
        pdf.new_page().insert_text((72, 72), 'Second page shared-term second-only')
        pdf.set_toc([[1, 'First section', 1], [1, 'Second section', 2]])
        binary = pdf.tobytes()
        pdf.close()
        file_route = f'**/knowledge/files/{quote(args.paper_id, safe="")}'
        page.route(file_route, lambda route: route.fulfill(body=binary, content_type='application/pdf'))
        page.evaluate('(id) => openReader(id)', args.paper_id)
        page.wait_for_function("document.getElementById('rrCount').textContent === '/ 2'")
        page.locator('#rrNext').click()
        page.wait_for_function("document.getElementById('rrPage').value === '2'")
        page.locator('#rrPrev').click()
        page.wait_for_function("document.getElementById('rrPage').value === '1'")
        page.locator('#rrSearch').click()
        page.locator('#rrFindInput').fill('second-only')
        page.wait_for_function("document.getElementById('rrFindStatus').textContent === '1 / 1' && document.getElementById('rrPage').value === '2'")
        page.locator('#rrToggleLeft').click()
        page.locator('[data-rr-nav="outline"]').click()
        page.locator('[data-outline="0"]').click()
        page.wait_for_function("document.getElementById('rrPage').value === '1'")
        page.unroute(file_route)
        print('PASS: fixture-only multi-page navigation, cross-page search and PDF outline links')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('(id) => openReader(id)', args.paper_id)
        page.locator('#rrPages canvas').wait_for()
        page.wait_for_timeout(500)
        assert page.locator('#rrBody').evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
        page.screenshot(path=str(args.output / 'reader-mobile.png'))
        assert not errors, errors
        print(f'PASS: real PDF ({pixels} nonwhite pixels), sidebar controls, text search and mobile layout')
        browser.close()


if __name__ == '__main__':
    main()
