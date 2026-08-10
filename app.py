
# -*- coding: utf-8 -*-
"""
게임숍 디스코드 봇 - 가격표 + 거래 내역 + 채널 제한 + 티켓 시스템 + 사용 등록 시스템 + 간편 로그인(OAuth2) 웹서버 통합
----------------------------------------------------------------------------------------------------
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask, request
import requests

load_dotenv()

# 환경변수 설정
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
DB_PATH = os.getenv("DB_PATH", "shop.db")
KST = timezone(timedelta(hours=9))

# 간편 로그인(OAuth2) 설정 (필요에 따라 .env로 분리하거나 직접 수정하세요)
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1535126290221367316")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "여기에_디스코드_앱_CLIENT_SECRET_입력")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://너의도메인주소.dishost.kr/callback")

intents = discord.Intents.default()
intents.members = True          # 멤버 프로필/역할 조회
intents.message_content = True  # !서버등록, !관리자등록 같은 접두사 명령어를 읽기 위해 필요


# ---------------------------------------------------------------------------
# 1. Flask 웹 서버 (간편 로그인 및 콜백 처리)
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def home():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html 파일이 업로드되지 않았습니다."


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "인증 실패: 코드가 존재하지 않습니다."

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(
        "https://discord.com/api/oauth2/token", data=data, headers=headers
    )
    tokens = response.json()

    access_token = tokens.get("access_token")
    if not access_token:
        return "토큰 발급에 실패했습니다."

    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_res = requests.get("https://discord.com/api/users/@me", headers=user_headers)
    user_data = user_res.json()
    username = user_data.get("username", "사용자")

    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>인증 성공</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center">
        <div class="max-w-md w-full mx-4 bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center shadow-2xl">
            <h1 class="text-2xl font-bold mb-2">인증 성공</h1>
            <p class="text-slate-400 text-sm mb-4"><b>{username}</b>님, 정상적으로 인증되었습니다.</p>
            <p class="text-slate-500 text-xs">이제 브라우저 창을 닫으셔도 됩니다.</p>
        </div>
    </body>
    </html>
    """


def run_flask():
    # Dishost 등 호스팅 환경 포트(8080)에 맞춰 실행
    app.run(host="0.0.0.0", port=8080)


# ---------------------------------------------------------------------------
# 2. 디스코드 봇 Gated Command Tree 설정
# ---------------------------------------------------------------------------
class GatedCommandTree(app_commands.CommandTree):
    """모든 슬래시 명령어 실행 전에 '서버 등록' + '관리자 등록' 여부를 확인한다."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "이 봇은 서버 안에서만 사용할 수 있어요.", ephemeral=True
            )
            return False

        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message(
                "⚠️ 이 서버는 아직 봇 사용 등록이 안 되어 있어요.\n"
                "서버 관리자가 `!서버등록` 명령어를 먼저 실행해주세요.",
                ephemeral=True,
            )
            return False

        if not is_admin(interaction):
            await interaction.response.send_message(
                "❌ 이 봇은 등록된 관리자만 사용할 수 있어요.\n"
                "서버 관리자에게 `!관리자등록` 을 요청하세요.",
                ephemeral=True,
            )
            return False

        return True


bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GatedCommandTree)


# ---------------------------------------------------------------------------
# 데이터베이스 초기화
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_channels (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, channel_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_config (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS active_tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            quantity INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS registered_guilds (
            guild_id INTEGER PRIMARY KEY,
            registered_by INTEGER NOT NULL,
            registered_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_admins (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 등록 관련 헬퍼
# ---------------------------------------------------------------------------
def is_guild_registered(guild_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM registered_guilds WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    conn.close()
    return row is not None


def register_guild(guild_id: int, by_id: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO registered_guilds (guild_id, registered_by, registered_at) VALUES (?, ?, ?)",
        (guild_id, by_id, now_kst_str()),
    )
    conn.commit()
    conn.close()


def is_bot_admin(guild_id: int, user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()
    conn.close()
    return row is not None


def add_bot_admin(guild_id: int, user_id: int, added_by: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO bot_admins (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, added_by, now_kst_str()),
    )
    conn.commit()
    conn.close()


def remove_bot_admin(guild_id: int, user_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM bot_admins WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_bot_admins(guild_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id FROM bot_admins WHERE guild_id = ?", (guild_id,)
    ).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# 권한 및 채널 체크 함수
# ---------------------------------------------------------------------------
def is_admin(ctx_or_interaction) -> bool:
    if isinstance(ctx_or_interaction, discord.Interaction):
        member = ctx_or_interaction.user
        guild_id = ctx_or_interaction.guild_id
    else:
        member = ctx_or_interaction.author
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None

    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    if any(role.name == ADMIN_ROLE_NAME for role in member.roles):
        return True
    if guild_id and is_bot_admin(guild_id, member.id):
        return True
    return False


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message(
            "❌ 이 명령어는 관리자만 사용할 수 있어요.", ephemeral=True
        )
        return False

    return app_commands.check(predicate)


def check_channel():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild_id:
            return True

        conn = get_conn()
        rows = conn.execute(
            "SELECT channel_id FROM allowed_channels WHERE guild_id = ?",
            (interaction.guild_id,)
        ).fetchall()
        conn.close()

        if not rows:
            return True

        allowed_channel_ids = [r["channel_id"] for r in rows]
        if interaction.channel_id in allowed_channel_ids:
            return True

        await interaction.response.send_message(
            f"⚠️ 이 명령어는 지정된 봇 사용 채널에서만 사용할 수 있어요.\n👉 허용된 채널: <#{'#>, <#'.join(map(str, allowed_channel_ids))}>",
            ephemeral=True
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


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.reply("⚠️ 해당 유저를 찾을 수 없어요. @멘션으로 지정해주세요.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("⚠️ 필요한 값이 빠졌어요. 사용법을 확인해주세요.")
        return
    print(f"접두사 명령어 오류: {error}")


# ---------------------------------------------------------------------------
# 등록 명령어 (!서버등록, !관리자등록, !관리자해제, !관리자목록)
# ---------------------------------------------------------------------------
@bot.command(name="서버등록")
async def register_server(ctx: commands.Context):
    if ctx.guild is None:
        await ctx.reply("이 명령어는 서버 안에서만 사용할 수 있어요.")
        return
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("❌ 이 명령어는 Discord 서버의 '관리자(Administrator)' 권한을 가진 사람만 사용할 수 있어요.")
        return
    if is_guild_registered(ctx.guild.id):
        await ctx.reply("✅ 이미 등록된 서버예요.")
        return

    register_guild(ctx.guild.id, ctx.author.id)
    add_bot_admin(ctx.guild.id, ctx.author.id, ctx.author.id)
    await ctx.reply(
        "✅ 서버 등록 완료! 이제 슬래시 명령어(`/가격표` 등)를 쓸 수 있어요.\n"
        f"({ctx.author.mention} 님은 자동으로 봇 관리자로 등록됐어요.)\n"
        "다른 사람도 봇을 쓰게 하려면 `!관리자등록 @유저` 로 추가해주세요."
    )


@bot.command(name="관리자등록")
async def register_admin(ctx: commands.Context, member: discord.Member = None):
    if ctx.guild is None:
        await ctx.reply("이 명령어는 서버 안에서만 사용할 수 있어요.")
        return
    if not is_guild_registered(ctx.guild.id):
        await ctx.reply("⚠️ 먼저 `!서버등록` 을 실행해주세요.")
        return
    if not is_admin(ctx):
        await ctx.reply("❌ 이 명령어는 등록된 관리자만 사용할 수 있어요.")
        return
    if member is None:
        await ctx.reply("사용법: `!관리자등록 @유저`")
        return

    add_bot_admin(ctx.guild.id, member.id, ctx.author.id)
    await ctx.reply(f"✅ {member.mention} 님을 봇 관리자로 등록했어요.")


@bot.command(name="관리자해제")
async def unregister_admin(ctx: commands.Context, member: discord.Member = None):
    if ctx.guild is None:
        await ctx.reply("이 명령어는 서버 안에서만 사용할 수 있어요.")
        return
    if not is_admin(ctx):
        await ctx.reply("❌ 이 명령어는 등록된 관리자만 사용할 수 있어요.")
        return
    if member is None:
        await ctx.reply("사용법: `!관리자해제 @유저`")
        return

    removed = remove_bot_admin(ctx.guild.id, member.id)
    if removed:
        await ctx.reply(f"🗑️ {member.mention} 님의 봇 관리자 권한을 해제했어요.")
    else:
        await ctx.reply(f"⚠️ {member.mention} 님은 등록된 봇 관리자가 아니에요.")


@bot.command(name="관리자목록")
async def list_admins(ctx: commands.Context):
    if ctx.guild is None:
        await ctx.reply("이 명령어는 서버 안에서만 사용할 수 있어요.")
        return
    if not is_admin(ctx):
        await ctx.reply("❌ 이 명령어는 등록된 관리자만 사용할 수 있어요.")
        return

    ids = list_bot_admins(ctx.guild.id)
    if not ids:
        await ctx.reply("등록된 봇 관리자가 없어요.")
        return
    mentions = "\n".join(f"<@{uid}>" for uid in ids)
    await ctx.reply(f"👑 등록된 봇 관리자:\n{mentions}")


# ---------------------------------------------------------------------------
# 채널 관리 명령어 (/방등록, /방해제)
# ---------------------------------------------------------------------------
@bot.tree.command(name="방등록", description="[관리자] 현재 채널을 봇 사용 가능 채널로 등록합니다.")
@admin_only()
async def register_channel(interaction: discord.Interaction):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO allowed_channels (guild_id, channel_id) VALUES (?, ?)",
            (interaction.guild_id, interaction.channel_id)
        )
        conn.commit()
        await interaction.response.send_message(
            f"✅ 성공적으로 이 채널(<#{interaction.channel_id}>)이 봇 사용 채널로 등록되었습니다.",
            ephemeral=True
        )
    finally:
        conn.close()


@bot.tree.command(name="방해제", description="[관리자] 현재 채널을 봇 사용 가능 채널에서 제외합니다.")
@admin_only()
async def unregister_channel(interaction: discord.Interaction):
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM allowed_channels WHERE guild_id = ? AND channel_id = ?",
        (interaction.guild_id, interaction.channel_id)
    )
    conn.commit()
    conn.close()

    if cur.rowcount > 0:
        await interaction.response.send_message(
            f"🗑️ 이 채널(<#{interaction.channel_id}>)이 봇 사용 채널에서 제외되었습니다.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "⚠️ 이 채널은 등록된 봇 사용 채널이 아닙니다.",
            ephemeral=True
        )


# ---------------------------------------------------------------------------
# 가격표 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="가격표", description="전체 상품 가격표를 보여줍니다.")
@check_channel()
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
@check_channel()
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
@check_channel()
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
@check_channel()
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
@check_channel()
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
@check_channel()
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
@check_channel()
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
@check_channel()
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
# 티켓 시스템 (로그채널설정, 패널생성, 상호작용)
# ---------------------------------------------------------------------------
@bot.tree.command(name="로그채널설정", description="[관리자] 티켓 마감 시 구매 로그가 전송될 채널을 설정합니다.")
@app_commands.describe(채널="로그를 받을 채널")
@admin_only()
async def set_ticket_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute(
        "INSERT INTO ticket_config (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?",
        (interaction.guild_id, 채널.id, 채널.id)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 구매 로그 채널이 {채널.mention} (으)로 설정되었습니다.", ephemeral=True)


class TicketSelect(discord.ui.Select):
    def __init__(self, options_list):
        super().__init__(placeholder="🛒 구매하실 상품을 선택해주세요!", min_values=1, max_values=1, options=options_list)

    async def callback(self, interaction: discord.Interaction):
        selected_item = self.values[0]
        guild = interaction.guild

        conn = get_conn()
        item_row = conn.execute(
            "SELECT * FROM prices WHERE guild_id = ? AND item = ?",
            (guild.id, selected_item)
        ).fetchone()
        conn.close()

        if not item_row:
            await interaction.response.send_message("⚠️ 해당 상품을 찾을 수 없습니다.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        for role in guild.roles:
            if role.permissions.administrator or role.name == ADMIN_ROLE_NAME:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"ticket-{interaction.user.name}-{selected_item}"
        channel_name = "".join(c for c in channel_name if c.isalnum() or c in "-_").lower()[:90]

        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)

        conn = get_conn()
        conn.execute(
            "INSERT INTO active_tickets (channel_id, guild_id, buyer_id, item, quantity) VALUES (?, ?, ?, ?, ?)",
            (ticket_channel.id, guild.id, interaction.user.id, selected_item, 1)
        )
        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="🎫 구매 티켓 생성 완료",
            description=f"{interaction.user.mention}님, 환영합니다!\n선택하신 상품: **{selected_item}** ({fmt_won(item_row['price'])})\n\n관리자가 내용을 확인 후 처리를 도와드립니다. 완료되면 관리자가 아래 버튼을 눌러 티켓을 마감합니다.",
            color=discord.Color.green()
        )
        view = TicketControlView()
        await ticket_channel.send(content=f"{interaction.user.mention}", embed=embed, view=view)

        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, options_list):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(options_list))


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 마감 및 구매 완료", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ 티켓 마감은 관리자만 가능합니다.", ephemeral=True)
            return

        guild = interaction.guild
        channel = interaction.channel

        conn = get_conn()
        ticket_row = conn.execute(
            "SELECT * FROM active_tickets WHERE channel_id = ?",
            (channel.id,)
        ).fetchone()

        if not ticket_row:
            await interaction.response.send_message("⚠️ 이 채널의 티켓 정보를 찾을 수 없습니다.", ephemeral=True)
            conn.close()
            return

        buyer_id = ticket_row["buyer_id"]
        item_name = ticket_row["item"]
        quantity = ticket_row["quantity"]

        item_row = conn.execute(
            "SELECT * FROM prices WHERE guild_id = ? AND item = ?",
            (guild.id, item_name)
        ).fetchone()

        if not item_row:
            await interaction.response.send_message("⚠️ 해당 상품 가격 정보를 찾을 수 없습니다.", ephemeral=True)
            conn.close()
            return

        unit_price = item_row["price"]
        total_price = unit_price * quantity

        config_row = conn.execute(
            "SELECT log_channel_id FROM ticket_config WHERE guild_id = ?",
            (guild.id,)
        ).fetchone()

        buyer_member = guild.get_member(buyer_id)
        buyer_name = str(buyer_member) if buyer_member else f"ID: {buyer_id}"
        buyer_avatar = buyer_member.display_avatar.url if buyer_member else (guild.icon.url if guild.icon else None)

        conn.execute(
            """
            INSERT INTO transactions
            (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild.id,
                buyer_id,
                buyer_name,
                item_name,
                quantity,
                unit_price,
                total_price,
                "티켓 구매 완료",
                now_kst_str(),
                str(interaction.user),
            ),
        )

        if item_row["stock"] != -1:
            conn.execute(
                "UPDATE prices SET stock = stock - ? WHERE guild_id = ? AND item = ?",
                (quantity, guild.id, item_name),
            )

        conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (channel.id,))
        conn.commit()
        conn.close()

        if config_row:
            log_channel = guild.get_channel(config_row["log_channel_id"])
            if log_channel:
                log_embed = discord.Embed(
                    title="🛒 구매 감사 로그",
                    description="구매해주셔서 감사합니다! 이용해주셔서 진심으로 기쁩니다.",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(KST)
                )
                if buyer_avatar:
                    log_embed.set_author(name=buyer_name, icon_url=buyer_avatar)
                else:
                    log_embed.set_author(name=buyer_name)

                log_embed.add_field(name="구매한 물품", value=item_name, inline=True)
                log_embed.add_field(name="수량", value=f"{quantity}개", inline=True)
                log_embed.add_field(name="구매 가격", value=fmt_won(total_price), inline=True)
                log_embed.add_field(name="인사말", value="구매 감사합니다! 🎉", inline=False)
                log_embed.set_footer(text=f"처리 관리자: {interaction.user}")

                await log_channel.send(embed=log_embed)

        await interaction.response.send_message("🔒 티켓이 마감되었습니다. 5초 뒤 채널이 삭제됩니다.", ephemeral=False)

        import asyncio
        await asyncio.sleep(5)
        try:
            await channel.delete()
        except Exception:
            pass


@bot.tree.command(name="티켓패널", description="[관리자] 상품 선택 기능이 포함된 티켓 생성 패널을 이 채널에 전송합니다.")
@admin_only()
@check_channel()
async def create_ticket_panel(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, price FROM prices WHERE guild_id = ? ORDER BY category, item",
        (interaction.guild_id,)
    ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("⚠️ 등록된 상품이 없습니다. 먼저 상품을 등록해주세요.", ephemeral=True)
        return

    options = []
    for r in rows:
        options.append(discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])}"))

    if len(options) > 25:
        options = options[:25]

    embed = discord.Embed(
        title="🛒 상품 구매 티켓 센터",
        description="아래 메뉴에서 **구매하실 상품을 선택**하시면 전용 티켓 채널이 생성됩니다!",
        color=discord.Color.blurple()
    )
    embed.set_footer(text="게임숍 티켓 시스템")

    view = TicketPanelView(options)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 티켓 패널이 생성되었습니다.", ephemeral=True)


# ---------------------------------------------------------------------------
# 슬래시 명령어 에러 핸들링
# ---------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return
    print(f"명령어 오류: {error}")
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ 오류가 발생했어요. 잠시 후 다시 시도해주세요.", ephemeral=True)


# ---------------------------------------------------------------------------
# 메인 실행부 (웹 서버 스레드 + 디스코드 봇 구동)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN 환경변수가 설정되지 않았어요. 환경변수에 등록해주세요.")
    
    # 1. Flask 웹 서버를 백그라운드 스레드로 실행
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("🌐 간편 로그인 웹 서버(Flask)가 백그라운드에서 실행되었습니다.")

    # 2. 디스코드 봇 실행
    bot.run(TOKEN)
