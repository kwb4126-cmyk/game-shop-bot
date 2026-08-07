# -*- coding: utf-8 -*-
"""
게임숍 디스코드 봇 - 가격표 + 거래 내역 + 채널 제한 + 티켓 시스템 통합 버전
------------------------------------------------------------------------
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
ADMIN_ROLE_NAME = os.getenv("Dino bot anmin", "허용")
DB_PATH = os.getenv("DB_PATH", "shop.db")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True  # 유저 프로필 및 멤버 정보 조회를 위해 필요
bot = commands.Bot(command_prefix="!", intents=intents)


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
    # 티켓 로그 채널 설정 테이블
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_config (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER NOT NULL
        )
        """
    )
    # 활성화된 티켓 정보 저장 테이블
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
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 권한 및 채널 체크 함수
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

        # 상품 정보 확인
        conn = get_conn()
        item_row = conn.execute(
            "SELECT * FROM prices WHERE guild_id = ? AND item = ?",
            (guild.id, selected_item)
        ).fetchone()
        conn.close()

        if not item_row:
            await interaction.response.send_message("⚠️ 해당 상품을 찾을 수 없습니다.", ephemeral=True)
            return

        # 티켓 채널 권한 설정
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # 관리자 권한을 가진 사람들도 티켓을 볼 수 있도록 처리
        for role in guild.roles:
            if role.permissions.administrator or role.name == ADMIN_ROLE_NAME:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"ticket-{interaction.user.name}-{selected_item}"
        # 특수문자 정제 (디스코드 채널 이름 규칙)
        channel_name = "".join(c for c in channel_name if c.isalnum() or c in "-_").lower()[:90]

        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)

        # 데이터베이스에 활성 티켓 기록 (기본 수량 1개)
        conn = get_conn()
        conn.execute(
            "INSERT INTO active_tickets (channel_id, guild_id, buyer_id, item, quantity) VALUES (?, ?, ?, ?, ?)",
            (ticket_channel.id, guild.id, interaction.user.id, selected_item, 1)
        )
        conn.commit()
        conn.close()

        # 티켓 채널 내부 메시지 전송
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

        # 상품 가격 조회
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

        # 로그 채널 확인
        config_row = conn.execute(
            "SELECT log_channel_id FROM ticket_config WHERE guild_id = ?",
            (guild.id,)
        ).fetchone()

        buyer_member = guild.get_member(buyer_id)
        buyer_name = str(buyer_member) if buyer_member else f"ID: {buyer_id}"
        buyer_avatar = buyer_member.display_avatar.url if buyer_member else guild.icon.url if guild.icon else None

        # 거래 내역 추가 및 재고 차감
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

        # 활성 티켓 데이터 삭제
        conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (channel.id,))
        conn.commit()
        conn.close()

        # 구매 로그 채널로 전송
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
        
        # 5초 후 채널 삭제
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
        label = f"{r['item']} ({fmt_won(r['price'])})"
        # 디스코드 셀렉트 메뉴 값 길이 제한(100자) 및 고유 식별자 처리
        if len(label) > 100:
            label = label[:100]
        options.append(discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])}"))

    # 최대 25개 제한 고려
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
# 에러 핸들링
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
        raise SystemExit("❌ .env 파일에 DISCORD_TOKEN을 설정해주세요.")
    bot.run(TOKEN)

from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "봇이 정상적으로 실행 중입니다!"

def run():
    # Render나 Koyeb은 보통 PORT 환경 변수를 자동으로 지정해 줍니다.
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ .env 파일에 DISCORD_TOKEN을 설정해주세요.")
    
    # 웹 서버 스레드 시작
    keep_alive()
    
    # 디스코드 봇 실행
    bot.run(TOKEN)
