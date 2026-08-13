import os
import time
import random
import json
import sqlite3
import pika
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
EDGE_PROFILE = os.path.join(BASE_DIR, "edge_profile")
LOCK_DB = os.path.join(BASE_DIR, "_global_lock.db")

PLATFORM_NAME = "deepseek"
LOCK_TIMEOUT = 43200
LOCK_POLL_INTERVAL = 10
LOCK_EXPIRE_SECONDS = 1800
HEARTBEAT_INTERVAL = 60

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", 5673))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "geo")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "geo123")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "geo.rpa.task.queue")

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8080")

os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")


def callback_backend(task_id, agent_name, question_content, thinking_content,
                     answer_content, source_info, image_url, status, error_msg=None):
    try:
        callback_url = BACKEND_BASE_URL + "/api/rpa/callback"
        payload = {
            "taskNo": str(task_id),
            "aiPlatform": str(agent_name),
            "questionText": str(question_content),
            "thinkingContent": str(thinking_content),
            "answerText": str(answer_content),
            "sourceInfo": str(source_info),
            "screenshotUrls": [image_url] if image_url else [],
            "status": status,
            "errorMsg": error_msg,
            "durationMs": 0
        }
        log(f"回调后端: {callback_url}")
        log(json.dumps(payload, ensure_ascii=False, indent=2))
        response = requests.post(
            callback_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if response.status_code == 200:
            log("回调成功")
        else:
            log(f"回调失败: {response.text}")
    except Exception as e:
        log(f"回调异常: {e}")


def upload_screenshot(local_path, task_id):
    try:
        upload_url = BACKEND_BASE_URL + "/api/rpa/upload"
        log(f"上传截图: {local_path}")
        with open(local_path, "rb") as f:
            files = {"file": (os.path.basename(local_path), f, "image/png")}
            data = {"taskId": task_id}
            response = requests.post(upload_url, files=files, data=data, timeout=60)
        if response.status_code == 200:
            result_data = response.json()
            if result_data.get("code") == 200 or result_data.get("success") is True:
                return result_data.get("data") or result_data.get("url")
            elif "data" in result_data:
                return result_data.get("data")
        log(f"上传失败: {response.text}")
        return None
    except Exception as e:
        log(f"上传异常: {e}")
        return None


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
        thinking_content = page.evaluate(
            """
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
            """
        )
        return thinking_content
    except Exception as e:
        log(f"提取思考内容失败: {e}")
        return ""


def extract_answer(page):
    log("提取回答内容")
    try:
        answer_content = page.evaluate(
            """
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
            """
        )
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

                    urls.push([
                        normalize(element.innerText) || "网页",
                        rawUrl
                    ]);
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


def process_question(page, task_id, agent_name, question, output_dir, result_id_map, index, total):
    log(f"[{index}/{total}] 处理问题: {question[:50]}...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)

    question_result_id = None
    if agent_name and agent_name in result_id_map:
        mapping = result_id_map[agent_name]
        if isinstance(mapping, dict) and question in mapping:
            question_result_id = mapping[question]

    screenshot_file = os.path.join(output_dir, f"{task_id}_{agent_name}_{timestamp}_{index}.png")

    thinking = ""
    answer = ""
    sources = []
    image_url = None

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
                state = button.evaluate(
                    """
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
                    """
                )
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
            raise Exception("回答生成超时")

        wait_page_stable(page)

        log("尝试展开来源...")
        open_sources(page)
        time.sleep(5)

        log("再次等待页面稳定...")
        wait_page_stable(page)
        time.sleep(3)

        log("截图...")
        success = full_screenshot(page, screenshot_file)
        if not success or not os.path.exists(screenshot_file):
            raise Exception("截图失败")

        log("提取内容...")
        thinking = extract_thinking(page)
        answer = extract_answer(page)
        sources = extract_sources(page)

        source_info = json.dumps(sources, ensure_ascii=False) if sources else ""

        image_url = upload_screenshot(screenshot_file, task_id)
        if not image_url:
            log("截图上传失败，使用本地路径回调")
            image_url = screenshot_file

        callback_backend(
            task_id=task_id,
            agent_name=agent_name,
            question_content=question,
            thinking_content=thinking,
            answer_content=answer,
            source_info=source_info,
            image_url=image_url,
            status="SUCCESS",
            error_msg=None
        )

        log(f"[{index}/{total}] 问题处理完成")

    except Exception as e:
        log(f"[{index}/{total}] 问题处理失败: {e}")
        source_info = json.dumps(sources, ensure_ascii=False) if sources else ""
        callback_backend(
            task_id=task_id,
            agent_name=agent_name,
            question_content=question,
            thinking_content=thinking,
            answer_content=answer,
            source_info=source_info,
            image_url=image_url,
            status="FAILED",
            error_msg=str(e)
        )


def _is_deepseek_agent(name):
    normalized = str(name).lower().strip()
    return normalized == "deepseek"


def init_lock_db():
    conn = sqlite3.connect(str(LOCK_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS global_lock (
            id INTEGER PRIMARY KEY CHECK(id=1),
            locked_by TEXT NOT NULL,
            locked_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def acquire_global_lock(platform_name):
    start = time.time()
    while time.time() - start < LOCK_TIMEOUT:
        try:
            conn = sqlite3.connect(str(LOCK_DB))
            try:
                conn.execute("BEGIN EXCLUSIVE")
                row = conn.execute(
                    "SELECT locked_by, locked_at FROM global_lock WHERE id=1"
                ).fetchone()
                if row:
                    holder, lock_time = row
                    conn.commit()
                    conn.close()
                    elapsed = time.time() - lock_time
                    if elapsed > LOCK_EXPIRE_SECONDS:
                        log(f"发现过期锁 (被 {holder} 持有 {elapsed:.0f}秒)，强制获取")
                        cleanup = sqlite3.connect(str(LOCK_DB))
                        cleanup.execute("DELETE FROM global_lock WHERE id=1")
                        cleanup.commit()
                        cleanup.close()
                        time.sleep(1)
                        continue
                    log(f"全局锁被 {holder} 持有 ({elapsed:.0f}秒)，等待 {LOCK_POLL_INTERVAL}秒...")
                    time.sleep(LOCK_POLL_INTERVAL)
                    continue
                conn.execute(
                    "INSERT INTO global_lock (id, locked_by, locked_at) VALUES (1, ?, ?)",
                    (platform_name, time.time())
                )
                conn.commit()
                conn.close()
                log(f"[{platform_name}] 获取全局锁成功")
                return True
            except Exception:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                time.sleep(LOCK_POLL_INTERVAL)
        except Exception as e:
            log(f"锁连接异常: {e}")
            time.sleep(LOCK_POLL_INTERVAL)
    log(f"[{platform_name}] 获取全局锁超时 ({LOCK_TIMEOUT}秒)")
    return False


def heartbeat_global_lock(platform_name):
    try:
        conn = sqlite3.connect(str(LOCK_DB))
        conn.execute("UPDATE global_lock SET locked_at=? WHERE id=1 AND locked_by=?",
                     (time.time(), platform_name))
        conn.commit()
        conn.close()
    except Exception:
        pass


def release_global_lock(platform_name):
    try:
        conn = sqlite3.connect(str(LOCK_DB))
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("DELETE FROM global_lock WHERE id=1 AND locked_by=?", (platform_name,))
        conn.commit()
        conn.close()
        log(f"[{platform_name}] 已释放全局锁")
    except Exception as e:
        log(f"释放全局锁异常: {e}")


def republish_messages(body_list):
    if not body_list:
        return
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials,
            connection_attempts=3,
            retry_delay=5
        )
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        for body in body_list:
            channel.basic_publish(
                exchange='',
                routing_key=RABBITMQ_QUEUE,
                body=body,
                properties=pika.BasicProperties(delivery_mode=2)
            )
        log(f"已重新发布 {len(body_list)} 条消息到队列")
        connection.close()
    except Exception as e:
        log(f"重新发布消息失败: {e}")


def fetch_message():
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=credentials,
        connection_attempts=3,
        retry_delay=5
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    method_frame, header_frame, body = channel.basic_get(queue=RABBITMQ_QUEUE, auto_ack=False)

    if not method_frame:
        connection.close()
        return None, None

    try:
        data = json.loads(body.decode("utf-8"))
        task_id = data.get("taskId")
        agent_list = data.get("agentList", [])
        result_id_map = data.get("resultIdMap", {})
        output_dir = data.get("outputDir", "")

        deepseek_agents = [a for a in agent_list if _is_deepseek_agent(a)]
        other_agents = [a for a in agent_list if not _is_deepseek_agent(a)]

        if not deepseek_agents:
            log(f"代理列表 {agent_list} 中没有 DeepSeek，交还队列给其他脚本")
            channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
            connection.close()
            return None, None

        republish_list = []
        if other_agents:
            other_data = dict(data)
            other_data["agentList"] = other_agents
            new_body = json.dumps(other_data, ensure_ascii=False).encode("utf-8")
            republish_list.append(new_body)
            log(f"将非 DeepSeek 代理 {other_agents} 拆出，处理完后重新发布")

        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        connection.close()

        question_list = []
        for q in data.get("questionList", []):
            if isinstance(q, dict):
                question_list.append(q.get("content", ""))
            else:
                question_list.append(str(q))

        return ({
            "taskId": task_id,
            "agentList": deepseek_agents,
            "questionList": question_list,
            "resultIdMap": result_id_map,
            "outputDir": output_dir
        }, republish_list)

    except Exception as e:
        log(f"解析消息失败: {e}")
        channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
        connection.close()
        raise


def main():
    log("启动 RabbitMQ 消费者")
    log(f"服务器: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
    log(f"队列: {RABBITMQ_QUEUE}")
    log(f"后端: {BACKEND_BASE_URL}")

    init_lock_db()
    log("全局锁数据库已初始化")

    log("打开浏览器...")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=EDGE_PROFILE,
            channel="msedge",
            headless=False,
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
        log("DeepSeek 已打开")

        if not wait_for_login(page):
            log("无法登录，退出")
            browser.close()
            return

        log("开始监听 RabbitMQ 队列...")
        while True:
            task = None
            republish_list = []
            try:
                if not acquire_global_lock(PLATFORM_NAME):
                    log("获取全局锁超时，10秒后重试...")
                    time.sleep(10)
                    continue

                try:
                    task, republish_list = fetch_message()
                except Exception as e:
                    log(f"获取消息异常: {e}")
                    release_global_lock(PLATFORM_NAME)
                    time.sleep(5)
                    continue

                if not task:
                    release_global_lock(PLATFORM_NAME)
                    log("队列暂无匹配消息，10秒后重试...")
                    time.sleep(10)
                    continue

                task_id = task["taskId"]
                agent_list = task["agentList"]
                question_list = task["questionList"]
                result_id_map = task["resultIdMap"]
                output_dir = task["outputDir"]

                log(f"收到任务: taskId={task_id}, 问题数={len(question_list)}, 代理数={len(agent_list)}")

                if not question_list:
                    log("问题列表为空，跳过")
                    republish_messages(republish_list)
                    release_global_lock(PLATFORM_NAME)
                    time.sleep(10)
                    continue

                try:
                    agent_name = agent_list[0] if agent_list else "deepseek"
                    last_heartbeat = 0

                    for idx, question in enumerate(question_list, start=1):
                        if not question:
                            log(f"问题 {idx} 为空，跳过")
                            continue

                        heartbeat_global_lock(PLATFORM_NAME)
                        last_heartbeat = time.time()

                        process_question(
                            page=page,
                            task_id=task_id,
                            agent_name=agent_name,
                            question=question,
                            output_dir=output_dir or SCREENSHOT_DIR,
                            result_id_map=result_id_map,
                            index=idx,
                            total=len(question_list)
                        )

                        wait_time = random.randint(40, 90)
                        log(f"等待{wait_time}秒后处理下一条...")
                        deadline = time.time() + wait_time
                        while time.time() < deadline:
                            time.sleep(1)
                            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                                heartbeat_global_lock(PLATFORM_NAME)
                                last_heartbeat = time.time()

                    log(f"任务 {task_id} 处理完成")

                finally:
                    release_global_lock(PLATFORM_NAME)

                republish_messages(republish_list)
                if republish_list:
                    log("已将非 DeepSeek 代理消息重新发布到队列")
                log("等待10秒后继续监听...")
                time.sleep(10)

            except pika.exceptions.AMQPConnectionError as e:
                log(f"RabbitMQ 连接错误: {e}, 5秒后重连...")
                time.sleep(5)
            except Exception as e:
                log(f"主循环异常: {e}, 10秒后重试...")
                try:
                    release_global_lock(PLATFORM_NAME)
                except Exception:
                    pass
                time.sleep(10)


if __name__ == "__main__":
    main()