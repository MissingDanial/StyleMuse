"""Browser flow against browser_server.py; no paid model requests."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.getenv("STYLEMUSE_QA_OUTPUT", str(ROOT / ".tmp" / "browser-qa")))
OUTPUT.mkdir(parents=True, exist_ok=True)


def main():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else None, headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:5367")
        page.wait_for_load_state("networkidle")
        expect(page.locator("#access-panel")).to_be_visible()
        page.locator("#access-password").fill("browser-password")
        page.get_by_role("button", name="进入写作工坊").click()
        expect(page.locator("#app-content")).to_be_visible()
        page.locator("#write-author").select_option("sample")
        page.locator("#write-topic").fill("故乡的狗")
        page.locator("#write-retrieval").uncheck()
        page.locator("#btn-write").click()
        expect(page.locator("#write-output")).to_contain_text("故乡的狗", timeout=10000)
        page.reload()
        expect(page.locator("#write-output")).to_contain_text("故乡的狗", timeout=10000)
        expect(page.locator("#write-saved-path")).to_contain_text("已保存", timeout=20000)
        expect(page.locator("#write-check")).to_contain_text("文字复用风险较高")
        page.locator("#write-check summary").click()
        expect(page.locator("#write-check .reuse-evidence").first).to_contain_text("固定示例")
        expect(page.locator("#write-check .reuse-evidence").first).to_contain_text("摆渡人")
        expect(page.locator("#write-review")).to_contain_text("篇幅或空内容检查未通过")
        form = page.locator(".reuse-review-form").first
        expect(form).to_be_visible()
        form.locator("select").select_option("uncertain")
        form.locator("textarea").fill("测试记录：需要核对事件顺序。")
        form.get_by_role("button", name="保存复核意见").click()
        expect(page.locator("#toast")).to_contain_text("复核意见已保存")
        page.reload()
        expect(page.locator("#write-saved-path")).to_contain_text("已保存", timeout=10000)
        page.locator("#write-check summary").click()
        expect(page.locator(".reuse-review-form textarea").first).to_have_value("测试记录：需要核对事件顺序。")
        page.screenshot(path=str(OUTPUT / "originality-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "evidence mobile overflow"
        page.screenshot(path=str(OUTPUT / "originality-mobile.png"), full_page=True, animations="disabled")
        page.locator(".reuse-review-form").first.screenshot(path=str(OUTPUT / "semantic-review-mobile.png"))
        page.set_viewport_size({"width":1440,"height":1000})
        expect(page.locator("#btn-rewrite")).to_be_enabled()
        page.locator("#btn-rewrite").click()
        expect(page.locator(".version-item")).to_have_count(2, timeout=15000)
        page.locator('[data-tab="history"]').click()
        expect(page.locator(".manuscript-card")).to_have_count(1, timeout=10000)
        page.locator(".manuscript-card").click()
        expect(page.locator("#history-detail")).to_be_visible()
        page.get_by_role("button", name="对比版本", exact=True).click()
        expect(page.locator("#history-comparison")).to_be_visible()
        expect(page.locator("#compare-diff")).not_to_have_text("两个版本正文相同")
        page.screenshot(path=str(OUTPUT / "history-desktop.png"), full_page=True, animations="disabled")
        first = page.locator("#history-version option").first.get_attribute("value")
        page.locator("#history-version").select_option(first)
        original = page.locator("#history-body").inner_text()
        page.get_by_role("button", name="恢复为新版本", exact=True).click()
        expect(page.locator(".version-item")).to_have_count(3, timeout=15000)
        expect(page.locator("#write-output")).to_have_text(original)
        page.locator('[data-tab="jobs"]').click()
        expect(page.locator(".task-row")).to_have_count(3, timeout=10000)
        page.screenshot(path=str(OUTPUT / "tasks-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width":390,"height":844})
        page.locator('[data-tab="history"]').click()
        expect(page.locator(".manuscript-card")).to_be_visible()
        expect(page.locator(".manuscript-card")).to_contain_text("3 稿")
        page.screenshot(path=str(OUTPUT / "history-mobile.png"), full_page=True, animations="disabled")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "mobile horizontal overflow"
        assert not errors, errors
        browser.close()
    report = {"passed":True,"flows":["password login","submit","reload while generating","source evidence","semantic review save and reload","hard length requirement","rewrite","history","compare","restore","task list","mobile layout"],"page_errors":errors}
    (OUTPUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
