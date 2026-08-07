# -*- coding: utf-8 -*-
"""
게임숍 디스코드 봇 - 가격표 관리 + 거래 내역 기록/조회
--------------------------------------------------------
기능 요약
1) 가격표
   /가격표            : 전체 가격표 조회 (누구나)
   /가격추가          : 상품 추가 (관리자)
   /가격수정          : 상품 가격/재고 수정 (관리자)
   /가격삭제          : 상품 삭제 (관리자)

2) 거래 내역
   /거래추가          : 거래 기록 추가 (관리자) - 재고 자동 차감
   /내거래내역        : 본인의 거래 내역 조회 (누구나)
   /전체거래내역      : 전체 거래 내역 조회 (관리자)
   /매출요약          : 총 매출/판매량 요약 (관리자)

관리자 판단 기준: 서버의 "관리자(Administrator)" 권한을 가진 사람
또는 ADMIN_ROLE_NAME 으로 지정한 역할을 가진 사람
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("04af614dd76389c1306538b6cbb1a909f1d4282cdec2890c5ca0887b1a006fe7")
# 관리자 권한이 없어도 이 역할 이름을 가진 사람은 관리자 명령어 사용 가능
ADMIN_ROLE_NAME = os.getenv("Dino bot anmin", "허용")
DB_PATH = os.getenv("DB_PATH", "shop.db")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# 데이터베이스
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            guild_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            category TEXT DEFAULT '기타',
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT -1,  -- -1 이면 무제한 재고
            PRIMARY KEY (guild_id, item)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            buyer_name TEXT NOT NULL,
            item TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            memo TEXT,
            created_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 권한 체크
# ---------------------------------------------------------------------------
def is_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message(
            "❌ 이 명령어는 관리자만 사용할 수 있어요.", ephemeral=True
        )
        return False

    return app_commands.check(predicate)


def fmt_won(n: int) -> str:
    return f"{n:,}원"


def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 이벤트
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")
    print(f"✅ 로그인 완료: {bot.user}")


# ---------------------------------------------------------------------------
# 가격표 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="가격표", description="전체 상품 가격표를 보여줍니다.")
async def price_list(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM prices WHERE guild_id = ? ORDER BY category, item",
        (interaction.guild_id,),
    ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("등록된 상품이 없어요.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🛒 가격표",
        color=discord.Color.blurple(),
        timestamp=datetime.now(KST),
    )

    categories = {}
    for r in rows:
        categories.setdefault(r["category"], []).append(r)

    for cat, items in categories.items():
        lines = []
        for it in items:
            stock_txt = "무제한" if it["stock"] == -1 else f"{it['stock']}개"
            lines.append(f"**{it['item']}** — {fmt_won(it['price'])} (재고: {stock_txt})")
        embed.add_field(name=f"📂 {cat}", value="\n".join(lines), inline=False)

    embed.set_footer(text="가격/재고는 변동될 수 있습니다.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="가격추가", description="[관리자] 새 상품을 등록합니다.")
@app_commands.describe(
    상품명="상품 이름", 가격="가격(원)", 카테고리="분류(예: 롤, 배그)", 재고="재고 수량 (비우면 무제한)"
)
@admin_only()
async def add_price(
    interaction: discord.Interaction,
    상품명: str,
    가격: int,
    카테고리: str = "기타",
    재고: int = -1,
):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, 상품명, 카테고리, 가격, 재고),
        )
        conn.commit()
        await interaction.response.send_message(
            f"✅ **{상품명}** 상품을 등록했어요. ({fmt_won(가격)}, 분류: {카테고리})"
        )
    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            f"⚠️ **{상품명}** 상품은 이미 존재해요. `/가격수정`을 사용해주세요.", ephemeral=True
        )
    finally:
        conn.close()


@bot.tree.command(name="가격수정", description="[관리자] 기존 상품의 가격/재고/분류를 수정합니다.")
@app_commands.describe(
    상품명="수정할 상품 이름", 가격="새 가격(비우면 유지)", 재고="새 재고(비우면 유지)", 카테고리="새 분류(비우면 유지)"
)
@admin_only()
async def edit_price(
    interaction: discord.Interaction,
    상품명: str,
    가격: int = None,
    재고: int = None,
    카테고리: str = None,
):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM prices WHERE guild_id = ? AND item = ?",
        (interaction.guild_id, 상품명),
    ).fetchone()

    if not row:
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품을 찾을 수 없어요.", ephemeral=True)
        conn.close()
        return

    new_price = 가격 if 가격 is not None else row["price"]
    new_stock = 재고 if 재고 is not None else row["stock"]
    new_cat = 카테고리 if 카테고리 is not None else row["category"]

    conn.execute(
        "UPDATE prices SET price = ?, stock = ?, category = ? WHERE guild_id = ? AND item = ?",
        (new_price, new_stock, new_cat, interaction.guild_id, 상품명),
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ **{상품명}** 수정 완료 → {fmt_won(new_price)}, 재고 {new_stock if new_stock != -1 else '무제한'}, 분류 {new_cat}"
    )


@bot.tree.command(name="가격삭제", description="[관리자] 상품을 삭제합니다.")
@app_commands.describe(상품명="삭제할 상품 이름")
@admin_only()
async def delete_price(interaction: discord.Interaction, 상품명: str):
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM prices WHERE guild_id = ? AND item = ?",
        (interaction.guild_id, 상품명),
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품을 찾을 수 없어요.", ephemeral=True)
    else:
        await interaction.response.send_message(f"🗑️ **{상품명}** 상품을 삭제했어요.")


# ---------------------------------------------------------------------------
# 거래 내역 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="거래추가", description="[관리자] 거래 내역을 기록합니다. (재고 자동 차감)")
@app_commands.describe(
    구매자="구매한 유저", 상품명="상품 이름", 수량="구매 수량", 메모="비고(선택)"
)
@admin_only()
async def add_transaction(
    interaction: discord.Interaction,
    구매자: discord.Member,
    상품명: str,
    수량: int = 1,
    메모: str = "",
):
    if 수량 <= 0:
        await interaction.response.send_message("⚠️ 수량은 1 이상이어야 해요.", ephemeral=True)
        return

    conn = get_conn()
    item_row = conn.execute(
        "SELECT * FROM prices WHERE guild_id = ? AND item = ?",
        (interaction.guild_id, 상품명),
    ).fetchone()

    if not item_row:
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품을 찾을 수 없어요.", ephemeral=True)
        conn.close()
        return

    if item_row["stock"] != -1 and item_row["stock"] < 수량:
        await interaction.response.send_message(
            f"⚠️ 재고 부족해요. (현재 재고: {item_row['stock']})", ephemeral=True
        )
        conn.close()
        return

    unit_price = item_row["price"]
    total_price = unit_price * 수량

    conn.execute(
        """
        INSERT INTO transactions
        (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interaction.guild_id,
            구매자.id,
            str(구매자),
            상품명,
            수량,
            unit_price,
            total_price,
            메모,
            now_kst_str(),
            str(interaction.user),
        ),
    )

    if item_row["stock"] != -1:
        conn.execute(
            "UPDATE prices SET stock = stock - ? WHERE guild_id = ? AND item = ?",
            (수량, interaction.guild_id, 상품명),
        )

    conn.commit()
    conn.close()

    embed = discord.Embed(title="🧾 거래 기록 완료", color=discord.Color.green())
    embed.add_field(name="구매자", value=구매자.mention, inline=True)
    embed.add_field(name="상품", value=상품명, inline=True)
    embed.add_field(name="수량", value=str(수량), inline=True)
    embed.add_field(name="합계", value=fmt_won(total_price), inline=True)
    if 메모:
        embed.add_field(name="메모", value=메모, inline=False)
    embed.set_footer(text=f"기록자: {interaction.user} | {now_kst_str()}")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="내거래내역", description="본인의 거래 내역을 조회합니다.")
async def my_transactions(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE guild_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT 20",
        (interaction.guild_id, interaction.user.id),
    ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("거래 내역이 없어요.", ephemeral=True)
        return

    lines = []
    total = 0
    for r in rows:
        lines.append(
            f"`{r['created_at']}` {r['item']} x{r['quantity']} = {fmt_won(r['total_price'])}"
        )
        total += r["total_price"]

    embed = discord.Embed(
        title=f"📋 {interaction.user.display_name}님의 거래 내역 (최근 20건)",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )
    embed.set_footer(text=f"최근 20건 누적 합계: {fmt_won(total)}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="전체거래내역", description="[관리자] 전체 거래 내역을 조회합니다.")
@app_commands.describe(구매자="특정 구매자만 필터링(선택)", 개수="조회할 최근 건수(기본 20)")
@admin_only()
async def all_transactions(
    interaction: discord.Interaction,
    구매자: discord.Member = None,
    개수: int = 20,
):
    conn = get_conn()
    if 구매자:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE guild_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT ?",
            (interaction.guild_id, 구매자.id, 개수),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (interaction.guild_id, 개수),
        ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("거래 내역이 없어요.", ephemeral=True)
        return

    lines = []
    total = 0
    for r in rows:
        lines.append(
            f"`{r['created_at']}` {r['buyer_name']} — {r['item']} x{r['quantity']} = {fmt_won(r['total_price'])}"
        )
        total += r["total_price"]

    # 디스코드 임베드 필드 길이 제한(4096) 고려해 필요시 잘라내기
    desc = "\n".join(lines)
    if len(desc) > 4000:
        desc = desc[:4000] + "\n...(생략)"

    embed = discord.Embed(
        title="📋 전체 거래 내역",
        description=desc,
        color=discord.Color.dark_orange(),
    )
    embed.set_footer(text=f"조회된 {len(rows)}건 합계: {fmt_won(total)}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="매출요약", description="[관리자] 전체 매출/판매량 요약을 보여줍니다.")
@admin_only()
async def sales_summary(interaction: discord.Interaction):
    conn = get_conn()
    total_row = conn.execute(
        "SELECT COALESCE(SUM(total_price),0) as total, COUNT(*) as cnt FROM transactions WHERE guild_id = ?",
        (interaction.guild_id,),
    ).fetchone()

    top_items = conn.execute(
        """
        SELECT item, SUM(quantity) as qty, SUM(total_price) as revenue
        FROM transactions WHERE guild_id = ?
        GROUP BY item ORDER BY revenue DESC LIMIT 5
        """,
        (interaction.guild_id,),
    ).fetchall()
    conn.close()

    embed = discord.Embed(title="📊 매출 요약", color=discord.Color.gold())
    embed.add_field(name="총 매출", value=fmt_won(total_row["total"]), inline=True)
    embed.add_field(name="총 거래 건수", value=f"{total_row['cnt']}건", inline=True)

    if top_items:
        lines = [
            f"{i+1}. **{r['item']}** — {r['qty']}개 판매 / {fmt_won(r['revenue'])}"
            for i, r in enumerate(top_items)
        ]
        embed.add_field(name="인기 상품 TOP 5", value="\n".join(lines), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# 에러 핸들링
# ---------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return  # admin_only()에서 이미 메시지 보냄
    print(f"명령어 오류: {error}")
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ 오류가 발생했어요. 잠시 후 다시 시도해주세요.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ .env 파일에 DISCORD_TOKEN을 설정해주세요.")
    bot.run(TOKEN)
