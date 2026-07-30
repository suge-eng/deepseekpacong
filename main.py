import os
import time
import random
import pandas as pd
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
EDGE_PROFILE = os.path.join(BASE_DIR, "edge_profile")
EXCEL = os.path.join(BASE_DIR, "aaa.xlsx")

os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

def human_wait(a=1, b=3):
    time.sleep(random.randint(a, b))

def wait_answer_finish(page):
    log("等待回答完成")
    start = time.time()
    last_text = ""
    stable_count = 0
    last_height = 0
    height_stable = 0
    
    while True:
        if time.time() - start > 600:
            log("回答超时")
            return False
        
        try:
            thinking_btn = page.get_by_text("已思考", exact=False)
            send_btn = page.locator('button[type="submit"]')
            
            if thinking_btn.count() > 0:
                log("检测到思考按钮，回答已完成")
                time.sleep(5)
                return True
            
            if send_btn.count() > 0:
                try:
                    if send_btn.first.is_enabled():
                        log("检测到发送按钮可用，回答已完成")
                        time.sleep(5)
                        return True
                except:
                    pass
            
            text = page.locator("body").inner_text(timeout=3000)
            current_length = len(text)
            
            if text == last_text:
                stable_count += 1
            else:
                stable_count = 0
                last_text = text
            
            try:
                current_height = page.evaluate("document.documentElement.scrollHeight")
                if current_height == last_height:
                    height_stable += 1
                else:
                    height_stable = 0
                    last_height = current_height
            except:
                pass
            
            if stable_count >= 10 or height_stable >= 15:
                log("内容不再变化，回答完成")
                time.sleep(5)
                return True
            
            elapsed = int(time.time() - start)
            if elapsed % 30 == 0:
                log(f"正在生成中... ({elapsed}秒)")
            
        except Exception as e:
            log(f"检测异常: {e}")
        
        time.sleep(2)

def wait_page_stable(page):
    log("等待页面渲染")
    old_height = 0
    stable = 0
    
    for i in range(30):
        try:
            height = page.evaluate("document.body.scrollHeight")
            if height == old_height:
                stable += 1
            else:
                stable = 0
            
            if stable >= 3:
                log("页面稳定")
                return True
            
            old_height = height
        except:
            pass
        
        time.sleep(1)
    
    return False

def open_thinking(page):
    log("等待思考按钮")
    for i in range(30):
        try:
            btn = page.get_by_text("已思考", exact=False)
            if btn.count() > 0:
                btn.last.click()
                time.sleep(3)
                log("思考已展开")
                return True
        except:
            pass
        
        time.sleep(1)
    
    log("没有找到思考")
    return False

def extract_thinking(page):
    log("提取思考内容")
    try:
        thinking_content = page.evaluate("""
            (function() {
                const divs = document.querySelectorAll('div.ds-markdown');
                for (let div of divs) {
                    const text = div.textContent;
                    if (text && text.includes('思考') && text.length > 100) {
                        return text.trim();
                    }
                }
                const allDivs = document.querySelectorAll('div');
                for (let div of allDivs) {
                    const text = div.textContent;
                    if (text && text.includes('思考') && !text.includes('深度思考') && text.length > 100) {
                        return text.trim();
                    }
                }
                return '';
            })()
        """)
        return thinking_content
    except Exception as e:
        log(f"提取思考内容失败: {e}")
        return ""

def extract_answer(page):
    log("提取回答内容")
    try:
        answer_content = page.evaluate("""
            (function() {
                const div = document.querySelector('div.ds-markdown.ds-assistant-message-main-content');
                if (div) {
                    return div.textContent.trim();
                }
                const divs = document.querySelectorAll('div.ds-markdown');
                for (let div of divs) {
                    if (!div.classList.contains('ds-assistant-message-main-content')) {
                        const text = div.textContent;
                        if (text && text.length > 200 && !text.includes('思考')) {
                            return text.trim();
                        }
                    }
                }
                return '';
            })()
        """)
        return answer_content
    except Exception as e:
        log(f"提取回答内容失败: {e}")
        return ""

def extract_sources(page):
    log("提取网页来源")
    try:
        result = page.evaluate(
            """
            () => {
                const normalize = text =>
                    (text || "")
                    .replace(/\\r/g, "")
                    .replace(/[ \\t]+\\n/g, "\\n")
                    .trim();

                const visible = el => {
                    if (!el) return false;

                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();

                    return (
                        style.display !== "none" &&
                        style.visibility !== "hidden" &&
                        rect.width > 5 &&
                        rect.height > 5
                    );
                };

                const headings = [
                    ...document.querySelectorAll("body *")
                ].filter(el =>
                    visible(el) &&
                    normalize(el.innerText) === "搜索结果"
                );

                const heading = headings.at(-1);

                if (!heading) {
                    return {
                        panelText: "",
                        urls: []
                    };
                }

                const headingRect = heading.getBoundingClientRect();

                let node = heading.parentElement;
                let panel = null;

                for (let i = 0; node && i < 12; i++) {
                    const rect = node.getBoundingClientRect();
                    const text = normalize(node.innerText);

                    if (
                        rect.width > 250 &&
                        rect.height > 200 &&
                        rect.left >= headingRect.left - 50 &&
                        text.length > 100
                    ) {
                        panel = node;
                        break;
                    }

                    node = node.parentElement;
                }

                if (!panel) {
                    return {
                        panelText: "",
                        urls: []
                    };
                }

                let panelText = normalize(panel.innerText);

                if (panelText.startsWith("搜索结果")) {
                    panelText = panelText
                        .slice("搜索结果".length)
                        .trim();
                }

                const elements = [
                    ...panel.querySelectorAll(
                        "a[href], [data-href], [data-url], [data-link]"
                    )
                ];

                const urls = [];
                const seen = new Set();

                for (const element of elements) {
                    if (!visible(element)) {
                        continue;
                    }

                    const rect = element.getBoundingClientRect();

                    if (
                        rect.left < headingRect.left - 50 ||
                        rect.right > window.innerWidth + 10
                    ) {
                        continue;
                    }

                    let rawUrl =
                        element.href ||
                        element.getAttribute("data-href") ||
                        element.getAttribute("data-url") ||
                        element.getAttribute("data-link") ||
                        "";

                    if (!rawUrl) {
                        continue;
                    }

                    try {
                        rawUrl = new URL(
                            rawUrl,
                            window.location.href
                        ).href;
                    } catch (error) {
                        continue;
                    }

                    if (
                        !rawUrl.startsWith("http://") &&
                        !rawUrl.startsWith("https://")
                    ) {
                        continue;
                    }

                    if (
                        rawUrl.includes("chat.deepseek.com") ||
                        rawUrl.includes("deepseek.com/a/")
                    ) {
                        continue;
                    }

                    if (seen.has(rawUrl)) {
                        continue;
                    }

                    seen.add(rawUrl);

                    urls.push({
                        title: normalize(element.innerText) || "网页",
                        url: rawUrl
                    });
                }

                return {
                    panelText: panelText,
                    urls: urls
                };
            }
            """
        )

        if not result:
            return []

        urls = result.get("urls") or []
        return urls

    except Exception as e:
        log(f"提取网页来源失败: {e}")
        return []

def open_sources(page):
    log("等待来源按钮")
    for i in range(30):
        try:
            btn = page.get_by_text("个网页", exact=False)
            if btn.count() > 0:
                btn.first.click()
                time.sleep(3)
                log("来源已展开")
                return True
        except:
            pass
        
        time.sleep(1)
    
    log("没有来源")
    return False

def full_screenshot(page, path):
    log(f"长截图:{path}")
    
    try:
        log("步骤1: 等待页面完全加载")
        page.wait_for_load_state("networkidle")
        time.sleep(5)
        
        log("步骤2: 滚动到页面底部加载所有内容")
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            time.sleep(4)
        
        log("步骤3: 等待所有内容渲染")
        time.sleep(10)
        
        log("步骤4: 获取最终页面尺寸")
        total_height = page.evaluate("document.documentElement.scrollHeight")
        log(f"最终页面高度: {total_height}px")
        
        log("步骤5: 截取整个页面")
        page.screenshot(path=path, full_page=True)
        
        log("步骤6: 裁剪图片")
        img = Image.open(path)
        log(f"原始图片尺寸: {img.width}x{img.height}")
        
        left_sidebar_width = 280
        
        pixels = img.load()
        content_bottom = img.height
        
        consecutive_empty_lines = 0
        max_empty_lines = 50
        content_threshold = 5
        
        for y in range(img.height - 1, 0, -1):
            content_pixels = 0
            for x in range(left_sidebar_width, img.width):
                r, g, b = pixels[x, y]
                if (r, g, b) != (255, 255, 255):
                    content_pixels += 1
                    if content_pixels >= content_threshold:
                        break
            
            if content_pixels >= content_threshold:
                consecutive_empty_lines = 0
            else:
                consecutive_empty_lines += 1
            
            if consecutive_empty_lines >= max_empty_lines:
                content_bottom = y + max_empty_lines + 30
                break
        
        cropped_img = img.crop((left_sidebar_width, 0, img.width, content_bottom))
        cropped_img.save(path)
        log(f"裁剪后图片尺寸: {cropped_img.width}x{cropped_img.height}")
        
        log(f"长截图完成: {path}")
        return True
    
    except Exception as e:
        log(f"截图失败:{e}")
        return False

def is_logged_in(page):
    try:
        login_btn = page.get_by_text("登录", exact=False)
        if login_btn.count() > 0:
            log("检测到登录按钮，未登录")
            return False
        
        signin_btn = page.get_by_text("Sign In", exact=False)
        if signin_btn.count() > 0:
            log("检测到Sign In按钮，未登录")
            return False
        
        textarea = page.locator("textarea")
        if textarea.count() > 0:
            log("检测到输入框，已登录")
            return True
        
        new_btn = page.get_by_text("新对话", exact=False)
        if new_btn.count() > 0:
            log("检测到新对话按钮，已登录")
            return True
        
        return False
    except Exception as e:
        log(f"登录检测异常: {e}")
        return False

def wait_for_login(page):
    log("等待登录...")
    for i in range(120):
        if is_logged_in(page):
            log("已登录")
            return True
        time.sleep(1)
    log("登录超时")
    return False

def new_chat(page):
    log("新建对话")
    try:
        new_btn = page.get_by_text("新对话", exact=False)
        if new_btn.count() > 0:
            new_btn.first.click()
            time.sleep(3)
            return True
        
        plus_btn = page.locator('button:has(svg)')
        if plus_btn.count() > 0:
            plus_btn.first.click()
            time.sleep(3)
            return True
    except Exception as e:
        log(f"新建对话失败:{e}")
    
    return False

def main():
    if not os.path.exists(EXCEL):
        log(f"Excel文件不存在: {EXCEL}")
        return
    
    df = pd.read_excel(EXCEL)
    log(f"共读取到 {len(df)} 条任务")
    
    if "status" in df.columns:
        pending_tasks = df[df["status"] != 1]
    else:
        pending_tasks = df
    log(f"待处理任务: {len(pending_tasks)} 条")
    
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=EDGE_PROFILE,
            channel="msedge",
            headless=True,
            viewport={"width": 1920, "height": 6000},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        if browser.pages:
            page = browser.pages[0]
        else:
            page = browser.new_page()
        
        page.goto("https://chat.deepseek.com", wait_until="domcontentloaded")
        log("DeepSeek打开")
        
        if not wait_for_login(page):
            log("无法登录，退出")
            browser.close()
            return
        
        log("开始处理任务...")
        for index, row in df.iterrows():
            status = row.get("status", 0)
            qid = row.get("id", f"未知_{index}")
            question = row.get("question", "")
            
            log(f"任务 {index+1}/{len(df)}: id={qid}, status={status}")
            
            if status == 1:
                log(f"任务 {qid} 已完成，跳过")
                continue
            
            if not question:
                log(f"任务 {qid} 问题为空，跳过")
                continue
            
            log(f"开始任务:{qid}")
            
            try:
                new_chat(page)
                human_wait(2, 5)
                
                textarea = page.locator("textarea")
                textarea.wait_for(timeout=60000)
                textarea.fill(question)
                
                log("尝试启用深度思考")
                try:
                    locator = page.get_by_text("深度思考", exact=True)
                    
                    for i in range(locator.count()):
                        button = locator.nth(i)
                        
                        if not button.is_visible():
                            continue
                        
                        state = button.evaluate("""
                            el => {
                                const node =
                                    el.closest('button,[role="button"]')
                                    || el.parentElement
                                    || el;

                                const values = [
                                    node.getAttribute('aria-pressed'),
                                    node.getAttribute('aria-selected'),
                                    node.getAttribute('data-selected'),
                                    node.getAttribute('data-active'),
                                    node.getAttribute('data-state')
                                ];

                                return values
                                    .filter(Boolean)
                                    .join('|')
                                    .toLowerCase();
                            }
                        """)
                        
                        if any(x in state for x in ["true", "on", "active", "selected"]):
                            log("深度思考已启用")
                            break
                        
                        if any(x in state for x in ["false", "off", "inactive"]):
                            button.click()
                            time.sleep(0.8)
                            log("已开启深度思考")
                            break
                        
                        button.click()
                        time.sleep(0.8)
                        log("已尝试开启深度思考")
                        break
                except Exception as e:
                    log(f"启用深度思考失败: {e}")
                
                textarea.press("Enter")
                
                ok = wait_answer_finish(page)
                if not ok:
                    raise Exception("回答失败")
                
                log("等待页面稳定...")
                wait_page_stable(page)
                
                # log("尝试展开思考...")
                # open_thinking(page)
                # time.sleep(5)
                
                log("尝试展开来源...")
                open_sources(page)
                time.sleep(5)
                
                log("再次等待页面稳定...")
                wait_page_stable(page)
                time.sleep(3)
                
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                
                answer_file = os.path.join(SCREENSHOT_DIR, f"answer_{qid}.png")
                log(f"准备截图: {answer_file}")
                
                page_height = page.evaluate("document.documentElement.scrollHeight")
                log(f"截图前页面高度: {page_height}px")
                success = full_screenshot(page, answer_file)
                if success:
                    if os.path.exists(answer_file):
                        file_size = os.path.getsize(answer_file) / 1024
                        log(f"截图成功，文件大小: {file_size:.2f} KB")
                    else:
                        log(f"截图成功，但文件不存在")
                else:
                    log(f"截图失败")
                
                log("提取内容...")
                thinking = extract_thinking(page)
                answer = extract_answer(page)
                sources = extract_sources(page)
                
                results_file = os.path.join(BASE_DIR, "results.xlsx")
                if os.path.exists(results_file):
                    results_df = pd.read_excel(results_file)
                else:
                    results_df = pd.DataFrame(columns=["id", "question", "thinking", "answer", "sources"])
                
                source_pairs = []
                for s in sources:
                    title = s.get("title", "")
                    url = s.get("url", "")
                    if title or url:
                        source_pairs.append(f"{title}\n{url}")
                
                sources_text = "\n\n".join(source_pairs) if source_pairs else ""
                
                new_row = pd.DataFrame({
                    "id": [qid],
                    "question": [question],
                    "thinking": [thinking],
                    "answer": [answer],
                    "sources": [sources_text]
                })
                results_df = pd.concat([results_df, new_row], ignore_index=True)
                results_df.to_excel(results_file, index=False)
                log(f"结果已保存到 {results_file}")
                
                df.loc[index, "status"] = 1
                df.to_excel(EXCEL, index=False)
                
                log(f"{qid}完成")
                
                wait_time = random.randint(40, 90)
                log(f"等待{wait_time}秒")
                time.sleep(wait_time)
            
            except Exception as e:
                log(f"任务失败:{e}")
                df.loc[index, "status"] = 0
                df.to_excel(EXCEL, index=False)
                time.sleep(60)
        
        browser.close()

if __name__ == "__main__":
    main()