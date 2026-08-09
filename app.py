# -*- coding: utf-8 -*-
"""
게임숍 디스코드 봇 - 가격표 + 거래 내역 + 채널 제한 + 티켓 시스템 + 사용 등록 시스템 + 관리자 알림 시스템
------------------------------------------------------------------------------------------------
- `/서버지정 #채널` 명령어로 로그 채널 설정 가능
- 티켓 마감 시 이미지처럼 가격, 아이템명, 구매시간, 구매 감사 메시지가 담긴 깔끔한 로그 임베드 출력
"""

import os
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
DB_PATH = os.getenv("DB_PATH", "shop.db")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True          
intents.message_content = True  


class GatedCommandTree(app_commands.CommandTree):
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
            stock INTEGER DEFAULT -1,
            min_quantity INTEGER DEFAULT 1,
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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_notifications (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            stock_alert INTEGER DEFAULT 1,
            event_alert INTEGER DEFAULT 1,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 등록 및 권한 헬퍼
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
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 재고 알림 발송 함수
# ---------------------------------------------------------------------------
async def send_stock_alert_to_admins(guild: discord.Guild, item_name: str, left_stock: int):
    if left_stock > 3 and left_stock != 0:
        return

    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id FROM admin_notifications WHERE guild_id = ? AND stock_alert = 1",
        (guild.id,)
    ).fetchall()
    conn.close()

    alert_user_ids = [r["user_id"] for r in rows]

    for member in guild.members:
        if member.bot:
            continue
        if member.guild_permissions.administrator or any(r.name == ADMIN_ROLE_NAME for r in member.roles):
            if member.id not in alert_user_ids:
                alert_user_ids.append(member.id)

    for uid in alert_user_ids:
        member = guild.get_member(uid)
        if member:
            try:
                embed = discord.Embed(
                    title="⚠️ [재고 임박/품절] 경고 알림",
                    description=f"상품 **{item_name}**의 재고가 위험합니다!\n현재 남은 재고: **{left_stock}개**",
                    color=discord.Color.red()
                )
                await member.send(embed=embed)
            except Exception:
                pass


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
# 알림 설정 시스템 (/알림설정 및 UI)
# ---------------------------------------------------------------------------
class NotificationSettingsView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.update_buttons()

    def update_buttons(self):
        conn = get_conn()
        row = conn.execute(
            "SELECT stock_alert, event_alert FROM admin_notifications WHERE guild_id = ? AND user_id = ?",
            (self.guild_id, self.user_id)
        ).fetchone()
        conn.close()

        stock_status = row["stock_alert"] if row else 1
        event_status = row["event_alert"] if row else 1

        self.stock_btn.label = f"📦 게임 재고 알림: {'ON 🟢' if stock_status else 'OFF 🔴'}"
        self.stock_btn.style = discord.ButtonStyle.success if stock_status else discord.ButtonStyle.secondary

        self.event_btn.label = f"🎉 이벤트 알림: {'ON 🟢' if event_status else 'OFF 🔴'}"
        self.event_btn.style = discord.ButtonStyle.success if event_status else discord.ButtonStyle.secondary

    @discord.ui.button(custom_id="toggle_stock", style=discord.ButtonStyle.success)
    async def stock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        row = conn.execute(
            "SELECT stock_alert FROM admin_notifications WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, interaction.user.id)
        ).fetchone()

        current = row["stock_alert"] if row else 1
        new_val = 0 if current == 1 else 1

        conn.execute(
            """
            INSERT INTO admin_notifications (guild_id, user_id, stock_alert, event_alert)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET stock_alert = ?
            """,
            (interaction.guild_id, interaction.user.id, new_val, new_val)
        )
        conn.commit()
        conn.close()

        self.update_buttons()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="toggle_event", style=discord.ButtonStyle.success)
    async def event_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        row = conn.execute(
            "SELECT event_alert FROM admin_notifications WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, interaction.user.id)
        ).fetchone()

        current = row["event_alert"] if row else 1
        new_val = 0 if current == 1 else 1

        conn.execute(
            """
            INSERT INTO admin_notifications (guild_id, user_id, stock_alert, event_alert)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET event_alert = ?
            """,
            (interaction.guild_id, interaction.user.id, new_val, new_val)
        )
        conn.commit()
        conn.close()

        self.update_buttons()
        await interaction.response.edit_message(view=self)


@bot.tree.command(name="알림설정", description="[관리자] 본인이 받을 봇 알림(재고, 이벤트 등)을 설정합니다.")
@admin_only()
@check_channel()
async def notification_settings(interaction: discord.Interaction):
    view = NotificationSettingsView(interaction.guild_id, interaction.user.id)
    embed = discord.Embed(
        title="🔔 관리자 알림 설정 센터",
        description="아래 버튼을 눌러 받고 싶은 알림을 켜고(🟢) 끌 수(🔴) 있습니다.\n(설정은 관리자 개인별로 저장됩니다.)",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# 가격표 명령어 (최소 주문 개수 추가)
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
            min_txt = f" (최소주문: {it['min_quantity']}개)" if it['min_quantity'] > 1 else ""
            lines.append(f"**{it['item']}** — {fmt_won(it['price'])} (재고: {stock_txt}){min_txt}")
        embed.add_field(name=f"📂 {cat}", value="\n".join(lines), inline=False)

    embed.set_footer(text="가격/재고는 변동될 수 있습니다.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="가격추가", description="[관리자] 새 상품을 등록합니다.")
@app_commands.describe(
    상품명="상품 이름", 가격="가격(원)", 카테고리="분류(예: 롤, 배그)", 재고="재고 수량 (비우면 무제한)", 최소주문수량="최소 주문 가능 수량 (기본 1)"
)
@admin_only()
@check_channel()
async def add_price(
    interaction: discord.Interaction,
    상품명: str,
    가격: int,
    카테고리: str = "기타",
    재고: int = -1,
    최소주문수량: int = 1,
):
    if 최소주문수량 < 1:
        최소주문수량 = 1

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO prices (guild_id, item, category, price, stock, min_quantity) VALUES (?, ?, ?, ?, ?, ?)",
            (interaction.guild_id, 상품명, 카테고리, 가격, 재고, 최소주문수량),
        )
        conn.commit()
        await interaction.response.send_message(
            f"✅ **{상품명}** 상품을 등록했어요. ({fmt_won(가격)}, 분류: {카테고리}, 최소주문: {최소주문수량}개)"
        )
    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            f"⚠️ **{상품명}** 상품은 이미 존재해요. `/가격수정`을 사용해주세요.", ephemeral=True
        )
    finally:
        conn.close()


@bot.tree.command(name="가격수정", description="[관리자] 기존 상품의 가격/재고/분류/최소주문을 수정합니다.")
@app_commands.describe(
    상품명="수정할 상품 이름", 가격="새 가격(비우면 유지)", 재고="새 재고(비우면 유지)", 카테고리="새 분류(비우면 유지)", 최소주문수량="새 최소주문수량(비우면 유지)"
)
@admin_only()
@check_channel()
async def edit_price(
    interaction: discord.Interaction,
    상품명: str,
    가격: int = None,
    재고: int = None,
    카테고리: str = None,
    최소주문수량: int = None,
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
    new_min = 최소주문수량 if 최소주문수량 is not None else row["min_quantity"]

    conn.execute(
        "UPDATE prices SET price = ?, stock = ?, category = ?, min_quantity = ? WHERE guild_id = ? AND item = ?",
        (new_price, new_stock, new_cat, new_min, interaction.guild_id, 상품명),
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ **{상품명}** 수정 완료 → {fmt_won(new_price)}, 재고 {new_stock if new_stock != -1 else '무제한'}, 분류 {new_cat}, 최소주문 {new_min}개"
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

    if 수량 < item_row["min_quantity"]:
        await interaction.response.send_message(f"⚠️ 이 상품의 최소 주문 수량은 **{item_row['min_quantity']}개** 이상입니다.", ephemeral=True)
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

    left_stock = item_row["stock"]
    if item_row["stock"] != -1:
        left_stock -= 수량
        conn.execute(
            "UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?",
            (left_stock, interaction.guild_id, 상품명),
        )

    conn.commit()
    conn.close()

    if left_stock != -1:
        await send_stock_alert_to_admins(interaction.guild, 상품명, left_stock)

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
# 티켓 시스템 (/서버지정, /티켓패널, /티켓닫기)
# ---------------------------------------------------------------------------
@bot.tree.command(name="서버지정", description="[관리자] 티켓 마감 시 구매 로그가 전송될 채널을 지정합니다.")
@app_commands.describe(채널="로그를 받을 채널")
@admin_only()
async def set_server_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute(
        "INSERT INTO ticket_config (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?",
        (interaction.guild_id, 채널.id, 채널.id)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 구매 로그 채널이 {채널.mention} (으)로 지정되었습니다.", ephemeral=True)


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

        min_qty = item_row["min_quantity"]

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
            (ticket_channel.id, guild.id, interaction.user.id, selected_item, min_qty)
        )
        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="🎫 구매 티켓 생성 완료",
            description=f"{interaction.user.mention}님, 환영합니다!\n선택하신 상품: **{selected_item}** ({fmt_won(item_row['price'])})\n최소 주문 수량: **{min_qty}개**\n\n관리자가 내용을 확인 후 처리를 도와드립니다. 완료되면 `/티켓닫기` 명령어를 입력해 마감할 수 있습니다.",
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
        await execute_close_ticket(interaction)


async def execute_close_ticket(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel

    conn = get_conn()
    ticket_row = conn.execute(
        "SELECT * FROM active_tickets WHERE channel_id = ?",
        (channel.id,)
    ).fetchone()

    if not ticket_row:
        await interaction.response.send_message("⚠️ 이 채널은 활성화된 티켓 채널이 아니거나 정보를 찾을 수 없습니다.", ephemeral=True)
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
    purchase_time_str = now_kst_str()

    config_row = conn.execute(
        "SELECT log_channel_id FROM ticket_config WHERE guild_id = ?",
        (guild.id,)
    ).fetchone()

    buyer_member = guild.get_member(buyer_id)
    buyer_name = str(buyer_member.name) if buyer_member else f"ID: {buyer_id}"
    buyer_avatar = buyer_member.display_avatar.url if buyer_member and buyer_member.display_avatar else (guild.icon.url if guild.icon else None)

    conn.execute(
        """
        INSERT INTO transactions
        (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild.id,
            buyer_id,
            str(buyer_member) if buyer_member else buyer_name,
            item_name,
            quantity,
            unit_price,
            total_price,
            "티켓 구매 완료",
            purchase_time_str,
            str(interaction.user),
        ),
    )

    left_stock = item_row["stock"]
    if item_row["stock"] != -1:
        left_stock -= quantity
        conn.execute(
            "UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?",
            (left_stock, guild.id, item_name),
        )

    conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (channel.id,))
    conn.commit()
    conn.close()

    if left_stock != -1:
        await send_stock_alert_to_admins(guild, item_name, left_stock)

    if config_row:
        log_channel = guild.get_channel(config_row["log_channel_id"])
        if log_channel:
            log_embed = discord.Embed(
                title="구매 완료",
                description=f"**구매자**\n{buyer_name}\n\n**게임명**\n{item_name}\n\n**가격**\n{fmt_won(total_price)}\n\n**구매시간**\n{purchase_time_str}\n\n구매해주셔서 감사합니다! 이용해주셔서 진심으로 기쁩니다. 🎉",
                color=discord.Color.from_rgb(255, 204, 0),
                timestamp=datetime.now(KST)
            )
            if buyer_avatar:
                log_embed.set_thumbnail(url=buyer_avatar)
            log_embed.set_footer(text=f"처리 관리자: {interaction.user}")

            await log_channel.send(embed=log_embed)

    await interaction.response.send_message("🔒 티켓이 마감되었습니다. 5초 뒤 채널이 삭제됩니다.", ephemeral=False)

    await asyncio.sleep(5)
    try:
        await channel.delete()
    except Exception:
        pass


@bot.tree.command(name="티켓닫기", description="[관리자] 티켓 채널 안에서 마감하고 거래 완료 및 재고 차감/로그 전송을 수행합니다.")
@admin_only()
async def close_ticket_command(interaction: discord.Interaction):
    await execute_close_ticket(interaction)


@bot.tree.command(name="티켓패널", description="[관리자] 상품 선택 기능이 포함된 티켓 생성 패널을 이 채널에 전송합니다.")
@admin_only()
@check_channel()
async def create_ticket_panel(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, price, min_quantity FROM prices WHERE guild_id = ? ORDER BY category, item",
        (interaction.guild_id,)
    ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("⚠️ 등록된 상품이 없습니다. 먼저 상품을 등록해주세요.", ephemeral=True)
        return

    options = []
    for r in rows:
        options.append(discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])} (최소: {r['min_quantity']}개)"))

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


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN 환경변수가 설정되지 않았어요. Discloud 환경변수에 등록해주세요.")
    bot.run(TOKEN)
