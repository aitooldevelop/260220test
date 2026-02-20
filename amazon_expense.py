#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Amazon.co.jp 経費精算ツール

注文履歴から経費精算データを抽出し、TSVファイルと領収書PDFとして保存する。

使い方:
    python amazon_expense.py --from 2024-01-01 --to 2024-12-31
"""

import argparse
import csv
import math
import os
import re
import sys
import time
from datetime import datetime, date
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright, Page, Browser


# Amazon.co.jp URLs
AMAZON_BASE = "https://www.amazon.co.jp"
AMAZON_ORDER_HISTORY = f"{AMAZON_BASE}/gp/your-account/order-history"
AMAZON_LOGIN = f"{AMAZON_BASE}/ap/signin"

# Output directory
OUTPUT_DIR = "output"
RECEIPTS_DIR = os.path.join(OUTPUT_DIR, "receipts")

# TSV headers
TSV_HEADERS = [
    "注文日",
    "品目",
    "店名",
    "税込価格",
    "税抜価格",
    "送料",
    "手数料",
    "ポイント値引き",
    "注文番号",
    "領収書PDF",
]


def sanitize_filename(name: str) -> str:
    """ファイル名に使えない文字を除去する。"""
    # Windows/Unix両方で問題になる文字を除去
    sanitized = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name)
    # 先頭・末尾の空白やドットを除去
    sanitized = sanitized.strip(" .")
    # 長すぎるファイル名を切り詰め
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    return sanitized


def parse_price(text: str) -> int:
    """価格テキストから数値を抽出する。「￥1,234」→ 1234"""
    if not text:
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def parse_japanese_date(text: str) -> str | None:
    """日本語の日付テキストをYYYY-MM-DDに変換する。

    対応フォーマット:
    - 2024年3月15日
    - 2024/3/15
    - 2024-03-15
    """
    if not text:
        return None

    # 「2024年3月15日」形式
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 「2024/3/15」形式
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 「2024-03-15」形式
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return None


def is_date_in_range(date_str: str, from_date: date, to_date: date) -> bool:
    """日付が指定範囲内か判定する。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return from_date <= d <= to_date
    except ValueError:
        return False


def ensure_output_dirs():
    """出力ディレクトリを作成する。"""
    os.makedirs(RECEIPTS_DIR, exist_ok=True)


def wait_for_login(page: Page):
    """ユーザーの手動ログインを待つ。"""
    print("\n" + "=" * 60)
    print("ブラウザでAmazonにログインしてください。")
    print("2段階認証やCAPTCHAがある場合も手動で対応してください。")
    print("ログインが完了したら、ここでEnterキーを押してください。")
    print("=" * 60)
    input("\n>>> Enterキーを押して続行...")
    print("自動取得を開始します...\n")


def navigate_to_order_history(page: Page, year: int):
    """注文履歴ページに遷移する（年指定）。"""
    params = {
        "orderFilter": f"year-{year}",
        "startIndex": "0",
        "disableCsd": "no-hierarchies",
    }
    url = f"{AMAZON_ORDER_HISTORY}?{urlencode(params)}"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)


def get_order_cards(page: Page) -> list:
    """注文履歴ページから注文カードを取得する。

    Amazonはクラス名を頻繁に変更するため、複数のセレクタを試行する。
    """
    selectors = [
        ".js-order-card",
        ".order-card",
        "#ordersContainer > .a-box-group",
        ".order-card__list > .js-order-card",
        "[class*='order-card']",
        ".a-box-group .a-box",
    ]

    for selector in selectors:
        cards = page.query_selector_all(selector)
        if cards:
            return cards

    # フォールバック: order-idを含むリンクの祖先要素を探す
    order_links = page.query_selector_all("a[href*='order-details']")
    if order_links:
        cards = []
        seen = set()
        for link in order_links:
            # 親のa-box-groupを探す
            parent = link.evaluate_handle(
                """el => {
                    let current = el;
                    for (let i = 0; i < 10; i++) {
                        current = current.parentElement;
                        if (!current) return null;
                        if (current.classList.contains('a-box-group') ||
                            current.classList.contains('order-card') ||
                            current.getAttribute('class')?.includes('order')) {
                            return current;
                        }
                    }
                    return null;
                }"""
            )
            if parent:
                identity = parent.evaluate("el => el.outerHTML.substring(0, 200)")
                if identity not in seen:
                    seen.add(identity)
                    cards.append(parent.as_element())
        if cards:
            return cards

    return []


def extract_order_number_from_card(card) -> str | None:
    """注文カードから注文番号を抽出する。"""
    # 注文番号のパターン: xxx-xxxxxxx-xxxxxxx（通常注文）またはDxx-xxxxxxx-xxxxxxx（デジタル注文）
    text = card.inner_text()
    m = re.search(r"[D\d]\d{2}-\d{7}-\d{7}", text)
    return m.group(0) if m else None


def extract_order_date_from_card(card) -> str | None:
    """注文カードから注文日を抽出する。"""
    text = card.inner_text()
    return parse_japanese_date(text)


def extract_order_total_from_card(card) -> int:
    """注文カードから合計金額を抽出する。"""
    text = card.inner_text()
    # 「合計 ￥1,234」や「注文合計: ￥1,234」を探す
    m = re.search(r"合計[:\s]*￥?([\d,]+)", text)
    if m:
        return int(m.group(1).replace(",", ""))
    # 「¥1,234」だけの場合
    m = re.search(r"[￥¥]([\d,]+)", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def get_order_detail_url(order_id: str) -> str:
    """注文詳細ページのURLを生成する。"""
    params = {"orderID": order_id}
    return f"{AMAZON_BASE}/gp/your-account/order-details?{urlencode(params)}"


def get_receipt_url(order_id: str) -> str:
    """領収書/購入明細書ページのURLを生成する。

    デジタル注文（注文番号が"D"で始まる）は別のURLパターンを使用する。
    """
    params = {"ie": "UTF8", "orderID": order_id}
    if order_id.startswith("D"):
        params["print"] = "1"
        return f"{AMAZON_BASE}/gp/digital/your-account/order-summary.html?{urlencode(params)}"
    return f"{AMAZON_BASE}/gp/css/summary/print.html?{urlencode(params)}"


def scrape_order_detail(page: Page, order_id: str, order_date: str) -> list[dict]:
    """注文詳細ページから商品情報を取得する。"""
    url = get_order_detail_url(order_id)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    items = []

    # 商品名を取得 - 複数のセレクタを試す
    product_elements = []
    product_selectors = [
        ".yohtmlc-product-title",
        "a[class*='product-title']",
        ".a-link-normal[href*='/dp/'] .a-text-bold",
        ".a-link-normal[href*='/gp/product/']",
        "[class*='item'] .a-link-normal",
    ]

    for selector in product_selectors:
        product_elements = page.query_selector_all(selector)
        if product_elements:
            break

    if not product_elements:
        # フォールバック: /dp/ リンクのテキストから商品名を取得
        product_elements = page.query_selector_all("a[href*='/dp/']")
        product_elements = [
            el for el in product_elements if el.inner_text().strip()
        ]

    # 販売元を取得
    seller = "Amazon.co.jp"
    seller_selectors = [
        ".yohtmlc-seller a",
        "[class*='seller'] a",
        "a[href*='seller']",
    ]
    for selector in seller_selectors:
        seller_el = page.query_selector(selector)
        if seller_el:
            seller_text = seller_el.inner_text().strip()
            if seller_text:
                seller = seller_text
                break

    # 全体のテキストから販売元を探す（フォールバック）
    if seller == "Amazon.co.jp":
        page_text = page.inner_text("body")
        m = re.search(r"(?:販売|出荷)[：:元]\s*(.+?)(?:\n|$)", page_text)
        if m:
            seller = m.group(1).strip()

    # 送料を取得
    shipping = 0
    page_text = page.inner_text("body")
    m = re.search(r"配送料[・&\s]*手数料[：:\s]*￥?([\d,]+)", page_text)
    if m:
        shipping = int(m.group(1).replace(",", ""))
    else:
        m = re.search(r"送料[：:\s]*￥?([\d,]+)", page_text)
        if m:
            shipping = int(m.group(1).replace(",", ""))

    # 手数料を取得
    fee = 0
    m = re.search(r"手数料[：:\s]*￥?([\d,]+)", page_text)
    if m:
        fee = int(m.group(1).replace(",", ""))

    # ポイント利用を取得
    points = 0
    m = re.search(r"ポイント[：:\s]*-?￥?([\d,]+)", page_text)
    if m:
        points = int(m.group(1).replace(",", ""))

    # 各商品の価格を取得
    price_elements = page.query_selector_all(
        ".a-color-price, [class*='price'] .a-text-bold"
    )
    prices = []
    for el in price_elements:
        price_text = el.inner_text().strip()
        price = parse_price(price_text)
        if price > 0:
            prices.append(price)

    # 商品ごとの情報を構築
    for i, product_el in enumerate(product_elements):
        product_name = product_el.inner_text().strip()
        if not product_name:
            continue

        # 価格の割り当て
        tax_included = prices[i] if i < len(prices) else 0

        # 税抜き価格（10%で計算）
        tax_excluded = math.floor(tax_included / 1.1) if tax_included else 0

        item = {
            "注文日": order_date,
            "品目": product_name,
            "店名": seller,
            "税込価格": tax_included,
            "税抜価格": tax_excluded,
            "送料": shipping if i == 0 else 0,  # 送料は最初の商品にのみ記録
            "手数料": fee if i == 0 else 0,
            "ポイント値引き": -points if i == 0 and points > 0 else 0,
            "注文番号": order_id,
            "領収書PDF": "",
        }
        items.append(item)

    # 商品が見つからなかった場合のフォールバック
    if not items:
        # ページタイトルや見出しから情報を取得
        title = page.title()
        items.append(
            {
                "注文日": order_date,
                "品目": f"(詳細取得失敗) 注文{order_id}",
                "店名": seller,
                "税込価格": 0,
                "税抜価格": 0,
                "送料": shipping,
                "手数料": fee,
                "ポイント値引き": -points if points > 0 else 0,
                "注文番号": order_id,
                "領収書PDF": "",
            }
        )

    return items


def save_receipt_pdf(page: Page, order_id: str, order_date: str, product_name: str) -> str:
    """領収書/購入明細書をPDFとして保存する。"""
    url = get_receipt_url(order_id)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    safe_name = sanitize_filename(product_name)
    filename = f"{order_date}_{safe_name}.pdf"
    filepath = os.path.join(RECEIPTS_DIR, filename)

    page.pdf(
        path=filepath,
        format="A4",
        print_background=True,
        margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
    )

    return filepath


def has_next_page(page: Page) -> bool:
    """次のページがあるか確認する。"""
    next_selectors = [
        "li.a-last:not(.a-disabled) a",
        ".a-pagination li.a-last a",
        "a:has-text('次へ')",
        "a:has-text('Next')",
    ]
    for selector in next_selectors:
        el = page.query_selector(selector)
        if el:
            return True
    return False


def go_to_next_page(page: Page) -> bool:
    """次のページに遷移する。"""
    next_selectors = [
        "li.a-last:not(.a-disabled) a",
        ".a-pagination li.a-last a",
        "a:has-text('次へ')",
        "a:has-text('Next')",
    ]
    for selector in next_selectors:
        el = page.query_selector(selector)
        if el:
            el.click()
            page.wait_for_timeout(2000)
            return True
    return False


def collect_order_ids_from_history(
    page: Page, year: int, from_date: date, to_date: date
) -> list[dict]:
    """注文履歴から注文情報を収集する。"""
    navigate_to_order_history(page, year)

    orders = []
    page_num = 1

    while True:
        print(f"  {year}年 ページ {page_num} を処理中...")
        cards = get_order_cards(page)

        if not cards:
            # カードが見つからない場合、ページ全体のテキストから注文番号を探す
            page_text = page.inner_text("body")
            order_ids = re.findall(r"[D\d]\d{2}-\d{7}-\d{7}", page_text)
            dates = re.findall(
                r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", page_text
            )

            for i, oid in enumerate(order_ids):
                if oid in [o["order_id"] for o in orders]:
                    continue
                if i < len(dates):
                    y, m, d = dates[i]
                    order_date = f"{y}-{int(m):02d}-{int(d):02d}"
                else:
                    order_date = f"{year}-01-01"

                if is_date_in_range(order_date, from_date, to_date):
                    orders.append(
                        {"order_id": oid, "order_date": order_date}
                    )

            if not order_ids:
                print(f"  注文が見つかりませんでした。")
                break
        else:
            for card in cards:
                order_id = extract_order_number_from_card(card)
                order_date = extract_order_date_from_card(card)

                if not order_id:
                    continue
                if order_id in [o["order_id"] for o in orders]:
                    continue
                if order_date and not is_date_in_range(
                    order_date, from_date, to_date
                ):
                    continue

                orders.append(
                    {
                        "order_id": order_id,
                        "order_date": order_date or f"{year}-01-01",
                    }
                )

        # 次のページへ
        if has_next_page(page):
            if not go_to_next_page(page):
                break
            page_num += 1
        else:
            break

    return orders


def write_tsv(records: list[dict], from_date: date, to_date: date):
    """TSVファイルを出力する（UTF-8 BOM付き）。"""
    from_str = from_date.strftime("%Y%m%d")
    to_str = to_date.strftime("%Y%m%d")
    filename = f"amazon_expenses_{from_str}_{to_str}.tsv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=TSV_HEADERS, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for record in records:
            writer.writerow(record)

    print(f"\nTSVファイルを保存しました: {filepath}")
    print(f"  合計 {len(records)} 件の商品データ")
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Amazon.co.jp 経費精算ツール - 注文履歴からTSV・領収書PDFを出力"
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        required=True,
        help="取得開始日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        required=True,
        help="取得終了日（YYYY-MM-DD）",
    )
    args = parser.parse_args()

    # 日付パース
    try:
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    except ValueError:
        print(f"エラー: 開始日の形式が不正です: {args.from_date}")
        print("  YYYY-MM-DD形式で指定してください（例: 2024-01-01）")
        sys.exit(1)

    try:
        to_date = datetime.strptime(args.to_date, "%Y-%m-%d").date()
    except ValueError:
        print(f"エラー: 終了日の形式が不正です: {args.to_date}")
        print("  YYYY-MM-DD形式で指定してください（例: 2024-12-31）")
        sys.exit(1)

    if from_date > to_date:
        print("エラー: 開始日は終了日より前の日付を指定してください。")
        sys.exit(1)

    # 出力先ディレクトリ作成
    ensure_output_dirs()

    print("Amazon.co.jp 経費精算ツール")
    print(f"期間: {from_date} ～ {to_date}")

    # 処理対象の年リストを生成
    years = list(range(from_date.year, to_date.year + 1))

    with sync_playwright() as pw:
        # ブラウザ起動（headedモード - ユーザーがログインできるように）
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        # Amazonログインページに遷移
        page.goto(f"{AMAZON_BASE}/gp/css/order-history", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # ユーザーの手動ログインを待つ
        wait_for_login(page)

        # 全注文を収集
        all_orders = []
        for year in years:
            print(f"\n{year}年の注文を取得中...")
            orders = collect_order_ids_from_history(page, year, from_date, to_date)
            print(f"  {len(orders)} 件の注文が見つかりました")
            all_orders.extend(orders)

        if not all_orders:
            print("\n指定期間内の注文が見つかりませんでした。")
            browser.close()
            sys.exit(0)

        print(f"\n合計 {len(all_orders)} 件の注文を処理します...")

        # 各注文の詳細を取得
        all_items = []
        for i, order in enumerate(all_orders, 1):
            order_id = order["order_id"]
            order_date = order["order_date"]
            print(f"\n[{i}/{len(all_orders)}] 注文 {order_id} を処理中...")

            # 注文詳細を取得
            items = scrape_order_detail(page, order_id, order_date)

            # 領収書PDFを保存（注文ごとに1つ）
            if items:
                first_item_name = items[0]["品目"]
                print(f"  領収書PDFを保存中...")
                try:
                    pdf_path = save_receipt_pdf(
                        page, order_id, order_date, first_item_name
                    )
                    # 全商品に同じPDFパスを設定
                    for item in items:
                        item["領収書PDF"] = pdf_path
                    print(f"  → {pdf_path}")
                except Exception as e:
                    print(f"  PDF保存エラー: {e}")

                for item in items:
                    print(f"  商品: {item['品目']} (￥{item['税込価格']:,})")

            all_items.extend(items)

            # レート制限対策
            time.sleep(1)

        # TSV出力
        write_tsv(all_items, from_date, to_date)

        # ブラウザを閉じる
        browser.close()

    print("\n処理が完了しました。")
    print(f"出力先: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
