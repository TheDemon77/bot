import logging
import sqlite3
import html
import re
import math
import os
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# ====================================================================
#                           إعدادات البوت (Configurations)
# ====================================================================

# ⚠️ أدخل بياناتك هنا بدقة
TOKEN = "8305359920:AAH96eYMX-eotR0l3kTxhn8YotDSU9_5vbk"  # 👈 ضع توكن البوت هنا
OWNER_ID = 8211646341             # 👈 ضع الآيدي الخاص بك
OWNER_USERNAME = "drvirus_6"     # 👈 يوزر المطور
CHANNEL_LINK = "https://t.me/MangaKingdom_AR" # 👈 رابط قناة الفهرس هنا
FORCE_CHANNEL_ID = -1003534146570        # 👈 آيدي القناة للاشتراك الإجباري (يبدأ بـ -100)

# اسم ملف قاعدة البيانات
DB_NAME = "manga_bot_v15_full.db"

# إعداد السجل (Logs) لتعقب الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# مراحل المحادثات (States)
(
    TITLE, GENRE, STATUS, RATING, DESC, PHOTO,           # مراحل إضافة مانجا
    SELECT_MANGA_INDEX, CHOOSE_TYPE, RECEIVE_FORWARDS,   # مراحل رفع الفصول
    DELETE_SELECT                                        # مراحل الحذف
) = range(10)


# ====================================================================
#                           قاعدة البيانات (Database)
# ====================================================================

def init_db():
    """تهيئة قاعدة البيانات وإنشاء الجداول إذا لم تكن موجودة."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 1. جدول المستخدمين
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 user_id INTEGER PRIMARY KEY, 
                 first_name TEXT,
                 points INTEGER DEFAULT 0,
                 is_admin INTEGER DEFAULT 0,
                 is_banned INTEGER DEFAULT 0
                 )''')
    
    # 2. تحديث هيكل الجدول (Migration) للمستخدمين القدامى
    try: c.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    except: pass

    # 3. جدول المانجا
    c.execute('''CREATE TABLE IF NOT EXISTS mangas (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 title TEXT,
                 genre TEXT,
                 status TEXT, 
                 rating TEXT, 
                 description TEXT, 
                 photo_id TEXT
                 )''')
    
    # 4. جدول الفصول
    c.execute('''CREATE TABLE IF NOT EXISTS chapters (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 manga_id INTEGER,
                 chapter_number TEXT,
                 file_id TEXT,
                 is_merged INTEGER DEFAULT 0,
                 FOREIGN KEY(manga_id) REFERENCES mangas(id)
                 )''')
                 
    # 5. جدول المفضلة
    c.execute('''CREATE TABLE IF NOT EXISTS favorites (
                 user_id INTEGER, 
                 manga_id INTEGER
                 )''')
    
    # إضافة المالك كأدمن رئيسي وحصانته من الحظر
    c.execute("INSERT OR IGNORE INTO users (user_id, is_admin, points) VALUES (?, 1, 999999)", (OWNER_ID,))
    c.execute("UPDATE users SET is_admin = 1, is_banned = 0 WHERE user_id = ?", (OWNER_ID,))
    
    conn.commit()
    conn.close()

def get_db():
    """دالة مساعدة للاتصال بقاعدة البيانات."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ====================================================================
#                           دوال مساعدة (Helpers)
# ====================================================================

def check_user_status(user_id):
    """التحقق هل المستخدم أدمن أم محظور."""
    conn = get_db()
    user = conn.execute("SELECT is_admin, is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user: 
        return {'is_admin': 0, 'is_banned': 0}
    return user

def add_points(user_id, first_name):
    """زيادة نقاط المستخدم (للمتصدرين)."""
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, first_name, points) VALUES (?, ?, 0)", (user_id, first_name))
    conn.execute("UPDATE users SET points = points + 1, first_name = ? WHERE user_id = ?", (first_name, user_id))
    conn.commit()
    conn.close()

def extract_chapter_number(text):
    """استخراج رقم الفصل أو المجلد (مثال: '0-20' من نص 'Vol 0-20')."""
    if not text: return "0"
    
    # 1. البحث عن نطاق مثل 0-20
    range_match = re.search(r'(\d+\s*-\s*\d+)', text)
    if range_match: 
        return range_match.group(1).replace(" ", "")
    
    # 2. البحث عن رقم عشري أو صحيح
    num_match = re.search(r'(\d+(\.\d+)?)', text)
    if num_match: 
        return num_match.group(1)
    
    return text[:15] # في أسوأ الظروف أعد جزء من النص

def sort_key(text):
    """دالة مساعدة للترتيب الطبيعي للأرقام."""
    nums = re.findall(r'\d+', text)
    return float(nums[0]) if nums else 0.0

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الاشتراك الإجباري في القناة."""
    user_id = update.effective_user.id
    
    # استثناء الأدمن والمالك
    if user_id == OWNER_ID or check_user_status(user_id)['is_admin']:
        return True
    
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_CHANNEL_ID, user_id=user_id)
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
            raise Exception("User not subscribed")
        return True
    except:
        msg = "⚠️ <b>عذراً يا صديقي، يجب الاشتراك في قناة البوت أولاً لتتمكن من استخدامه.</b>\n\nاضغط على الزر بالأسفل واشترك، ثم حاول مرة أخرى."
        keyboard = [[InlineKeyboardButton("📢 الاشتراك في القناة", url=CHANNEL_LINK)]]
        
        if update.callback_query:
            await update.callback_query.answer("⚠️ اشترك أولاً!", show_alert=True)
            # اختيارياً، يمكن إرسال الرسالة أيضاً:
            # await update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return False

# ====================================================================
#                           أوامر الإدارة (Admin Panel)
# ====================================================================

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة أوامر الأدمن."""
    if not check_user_status(update.effective_user.id)['is_admin']:
        return
    
    text = (
        "👑 <b>لوحة تحكم الأدمن الشاملة (V15)</b> 👑\n\n"
        "📢 <b>إدارة النشر:</b>\n"
        "• `/broadcast رسالة` - إرسال إذاعة لجميع المشتركين\n"
        "• `/stats` - عرض إحصائيات البوت الكاملة\n\n"
        "📚 <b>إدارة المحتوى:</b>\n"
        "• `/add` - إضافة مانجا جديدة\n"
        "• `/index` - رفع فصول (فردية أو مدمجة)\n"
        "• `/delete_manga` - حذف مانجا بالكامل\n\n"
        "👥 <b>إدارة الطاقم:</b>\n"
        "• `/promote [ID]` - ترقية عضو إلى أدمن\n"
        "• `/demote [ID]` - إعفاء أدمن\n"
        "• `/adminlist` - عرض قائمة الأدمنز الحاليين\n\n"
        "🚫 <b>الأمان:</b>\n"
        "• `/ban [ID]` - حظر مستخدم\n"
        "• `/unban [ID]` - رفع الحظر\n"
        "• `/backup` - تحميل نسخة احتياطية من الداتا\n"
        "• `/cancel` - لإيقاف أي عملية معلقة"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين."""
    if update.effective_user.id != OWNER_ID:
        return

    conn = get_db()
    admins = conn.execute("SELECT user_id, first_name FROM users WHERE is_admin = 1").fetchall()
    conn.close()
    
    msg = "👮‍♂️ <b>طاقم الإدارة (Admins):</b>\n\n"
    for admin in admins:
        name = html.escape(admin['first_name'] or "مستخدم")
        msg += f"🔹 <b>{name}</b> <code>{admin['user_id']}</code>\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت."""
    if not check_user_status(update.effective_user.id)['is_admin']: return
    
    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    mangas_count = conn.execute("SELECT COUNT(*) FROM mangas").fetchone()[0]
    chapters_count = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
    banned_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
    conn.close()
    
    msg = (
        "📊 <b>تقرير الإحصائيات:</b>\n\n"
        f"👥 عدد الأعضاء: <b>{users_count}</b>\n"
        f"📚 عدد المانجات: <b>{mangas_count}</b>\n"
        f"📄 عدد الفصول/الملفات: <b>{chapters_count}</b>\n"
        f"🚫 عدد المحظورين: <b>{banned_count}</b>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال رسالة جماعية (إذاعة)."""
    if update.effective_user.id != OWNER_ID: return
    
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("⚠️ <b>خطأ:</b> اكتب الرسالة بعد الأمر.\nمثال: `/broadcast تحديث جديد!`", parse_mode=ParseMode.HTML)
        return
    
    await update.message.reply_text("⏳ جاري بدء عملية النشر...")
    
    conn = get_db()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    
    success_count = 0
    blocked_count = 0
    
    for user in users:
        try:
            await context.bot.send_message(chat_id=user['user_id'], text=f"📢 <b>إعلان من الإدارة:</b>\n\n{message}", parse_mode=ParseMode.HTML)
            success_count += 1
        except:
            blocked_count += 1
            
    await update.message.reply_text(
        f"✅ <b>تم انتهاء الإذاعة.</b>\n\n"
        f"📤 وصلت لـ: {success_count}\n"
        f"❌ لم تصل لـ: {blocked_count} (قاموا بحظر البوت)", 
        parse_mode=ParseMode.HTML
    )

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال ملف قاعدة البيانات."""
    if update.effective_user.id != OWNER_ID: return
    try:
        await update.message.reply_document(
            document=open(DB_NAME, 'rb'),
            caption=f"📦 <b>نسخة احتياطية:</b> {DB_NAME}\nتاريخ: {context.args}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ أثناء النسخ: {e}")

# دوال إدارة الأشخاص (Promote, Demote, Ban, Unban)
async def promote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        user_id = int(context.args[0])
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم رفع المستخدم `{user_id}` لرتبة أدمن.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ الاستخدام: `/promote [ID]`", parse_mode=ParseMode.HTML)

async def demote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        user_id = int(context.args[0])
        conn = get_db()
        conn.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم تنزيل المستخدم `{user_id}` من الإدارة.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ الاستخدام: `/demote [ID]`", parse_mode=ParseMode.HTML)

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_user_status(update.effective_user.id)['is_admin']: return
    try:
        user_id = int(context.args[0])
        if user_id == OWNER_ID:
            await update.message.reply_text("❌ لا يمكنك حظر المالك!")
            return
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🚫 تم حظر المستخدم `{user_id}` بنجاح.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ الاستخدام: `/ban [ID]`", parse_mode=ParseMode.HTML)

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_user_status(update.effective_user.id)['is_admin']: return
    try:
        user_id = int(context.args[0])
        conn = get_db()
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم رفع الحظر عن المستخدم `{user_id}`.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ الاستخدام: `/unban [ID]`", parse_mode=ParseMode.HTML)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العمليات."""
    await update.message.reply_text("❌ <b>تم إلغاء العملية الحالية.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ====================================================================
#                           إضافة المانجا (Wizard)
# ====================================================================

async def admin_add_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_user_status(update.effective_user.id)['is_admin']: return ConversationHandler.END
    await update.message.reply_text("🆕 <b>إضافة مانجا جديدة</b>\n\n1️⃣ أرسل اسم المانجا:", parse_mode=ParseMode.HTML)
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['title'] = update.message.text
    await update.message.reply_text("2️⃣ أرسل التصنيف (مثلاً: أكشن، رعب):")
    return GENRE

async def get_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['genre'] = update.message.text
    await update.message.reply_text("3️⃣ أرسل الحالة (مثلاً: مستمر، مكتمل):")
    return STATUS

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['status'] = update.message.text
    await update.message.reply_text("4️⃣ أرسل التقييم (مثلاً: 9/10):")
    return RATING

async def get_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['rating'] = update.message.text
    await update.message.reply_text("5️⃣ أرسل قصة/وصف المانجا:")
    return DESC

async def get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['desc'] = update.message.text
    await update.message.reply_text("6️⃣ أخيراً.. أرسل صورة الغلاف:")
    return PHOTO

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("⚠️ يرجى إرسال صورة!")
        return PHOTO
    
    photo_id = update.message.photo[-1].file_id
    d = context.user_data
    
    conn = get_db()
    conn.execute("INSERT INTO mangas (title, genre, status, rating, description, photo_id) VALUES (?,?,?,?,?,?)",
                 (d['title'], d['genre'], d['status'], d['rating'], d['desc'], photo_id))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ <b>تم إضافة المانجا بنجاح للفهرس!</b>\nاستخدم /index الآن لإضافة الفصول.", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ====================================================================
#                           الأرشفة (رفع الفصول)
# ====================================================================

async def start_indexing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_user_status(update.effective_user.id)['is_admin']: return ConversationHandler.END
    
    conn = get_db()
    mangas = conn.execute("SELECT id, title FROM mangas").fetchall()
    conn.close()
    
    if not mangas:
        await update.message.reply_text("⚠️ المكتبة فارغة، أضف مانجا أولاً.")
        return ConversationHandler.END
        
    buttons = [[InlineKeyboardButton(m['title'], callback_data=f"selidx_{m['id']}")] for m in mangas]
    await update.message.reply_text("📂 <b>اختر المانجا لإضافة ملفات إليها:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    return SELECT_MANGA_INDEX

async def select_manga_for_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['index_manga_id'] = int(query.data.split('_')[1])
    
    buttons = [
        [InlineKeyboardButton("📄 فصول فردية (Single)", callback_data="type_normal")],
        [InlineKeyboardButton("📦 مجلدات مدمجة (Merged)", callback_data="type_merged")]
    ]
    await query.edit_message_text("⚙️ <b>اختر نوع الملفات التي سترفعها:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    return CHOOSE_TYPE

async def choose_upload_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_merged = 1 if query.data == "type_merged" else 0
    context.user_data['is_merged'] = is_merged
    
    type_text = "فصول مدمجة" if is_merged else "فصول فردية"
    
    await query.edit_message_text(
        f"⚡ <b>وضع الرفع: {type_text}</b>\n\n"
        "الآن قم بعمل <b>توجيه (Forward)</b> للملفات من القناة إلى هنا.\n"
        "عند الانتهاء تماماً، اضغط /done.",
        parse_mode=ParseMode.HTML
    )
    return RECEIVE_FORWARDS

async def archive_forwarded_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        return RECEIVE_FORWARDS
        
    manga_id = context.user_data['index_manga_id']
    is_merged = context.user_data['is_merged']
    doc = update.message.document
    
    # محاولة استخراج الاسم
    file_name = doc.file_name if doc.file_name else "file"
    caption = update.message.caption if update.message.caption else ""
    full_text = f"{file_name} {caption}"
    
    chapter_num = extract_chapter_number(full_text)
    
    conn = get_db()
    # التحقق من التكرار لتجنب الأخطاء
    exists = conn.execute("SELECT 1 FROM chapters WHERE manga_id=? AND file_id=? AND is_merged=?", (manga_id, doc.file_id, is_merged)).fetchone()
    
    if not exists:
        conn.execute("INSERT INTO chapters (manga_id, chapter_number, file_id, is_merged) VALUES (?, ?, ?, ?)",
                     (manga_id, chapter_num, doc.file_id, is_merged))
        conn.commit()
        # رسالة تأكيد صغيرة
        await update.message.reply_text(f"📥 <b>تم استلام: {chapter_num}</b>", quote=True, parse_mode=ParseMode.HTML)
    
    conn.close()
    return RECEIVE_FORWARDS

async def finish_indexing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ <b>تم حفظ جميع الفصول بنجاح.</b>\nشكراً لجهودك!", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ====================================================================
#                           حذف المانجا
# ====================================================================

async def start_delete_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_user_status(update.effective_user.id)['is_admin']: return ConversationHandler.END
    
    conn = get_db()
    mangas = conn.execute("SELECT id, title FROM mangas").fetchall()
    conn.close()
    
    if not mangas:
        await update.message.reply_text("📭 القائمة فارغة.")
        return ConversationHandler.END
        
    buttons = [[InlineKeyboardButton(f"🗑 {m['title']}", callback_data=f"del_{m['id']}")] for m in mangas]
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_del")])
    
    await update.message.reply_text("⚠️ <b>حذف مانجا</b>\nاختر المانجا للحذف النهائي:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
    return DELETE_SELECT

async def confirm_delete_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_del":
        await query.message.edit_text("❌ تم الإلغاء.")
        return ConversationHandler.END
    
    manga_id = int(query.data.split('_')[1])
    conn = get_db()
    
    conn.execute("DELETE FROM favorites WHERE manga_id = ?", (manga_id,))
    conn.execute("DELETE FROM chapters WHERE manga_id = ?", (manga_id,))
    conn.execute("DELETE FROM mangas WHERE id = ?", (manga_id,))
    conn.commit()
    conn.close()
    
    await query.message.edit_text("✅ <b>تم الحذف بنجاح.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# ====================================================================
#                           واجهة المستخدم (User Features)
# ====================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # فحص الحظر
    if check_user_status(user.id)['is_banned']:
        return

    # تسجيل نقاط
    add_points(user.id, user.first_name)
    
    text = (
        "🧾 <b>مرحبًا في بوت مانهاتك! (V15)</b>\n"
        "#1 اكبر مكتبة M3C على التلغـــرام 🔥\n"
        "لدينا : مانجا / مانهوا / كوميكس\n\n"
        "🔹 <b>أوامر سريعة:</b>\n"
        "<code>/search</code> للبحث عن مانجا\n"
        "<code>/request</code> لطلب مانجا معينة\n\n"
        "🔗 الفهرس العام : @manhwa_arab\n"
        "⛔️ <b>النسخة الكاملة</b>"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✨ مفضلتي", callback_data="my_favs"),
            InlineKeyboardButton("🏆 المتصدرين", callback_data="top_users")
        ],
        [
            InlineKeyboardButton("🎲 اقترح لي عمل", callback_data="random_manga")
        ],
        [
            InlineKeyboardButton("👨‍💻 مطور البوت", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("🪶 الفهرس", url=CHANNEL_LINK)
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        # التعامل مع تعديل الرسالة أو حذف الصورة
        if update.callback_query.message.photo:
            await update.callback_query.message.delete()
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# البحث الذكي
async def smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context): return
    if check_user_status(update.effective_user.id)['is_banned']: return
    
    # استخراج النص
    query = ""
    if context.args:
        query = " ".join(context.args)
    elif update.message and update.message.text and not update.message.text.startswith("/"):
        query = update.message.text
        
    if not query:
        await update.message.reply_text("🔍 <b>البحث:</b> أرسل اسم المانجا للبحث.", parse_mode=ParseMode.HTML)
        return

    add_points(update.effective_user.id, update.effective_user.first_name)
    
    conn = get_db()
    # بحث مرن (Lower Case)
    results = conn.execute("SELECT id, title FROM mangas WHERE LOWER(title) LIKE ?", (f'%{query.lower()}%',)).fetchall()
    conn.close()

    if not results:
        await update.message.reply_text("❌ لم يتم العثور على نتائج.\nتأكد من كتابة الاسم بشكل صحيح.", parse_mode=ParseMode.HTML)
        return

    if len(results) == 1:
        # لو نتيجة واحدة نفتحها مباشرة
        await show_manga_panel(update, context, results[0]['id'], 0, is_new=True)
    else:
        # لو أكثر من نتيجة
        keyboard = []
        for manga in results:
            keyboard.append([InlineKeyboardButton(f"📘 {manga['title']}", callback_data=f"panel_{manga['id']}_0")])
        
        await update.message.reply_text(f"🔎 <b>نتائج البحث عن:</b> {html.escape(query)}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

# طلب المانجا
async def request_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context): return
    if check_user_status(update.effective_user.id)['is_banned']: return
    
    request_text = " ".join(context.args)
    if not request_text:
        await update.message.reply_text("✍️ <b>اكتب اسم المانجا بعد الأمر.</b>\nمثال: `/request One Piece`", parse_mode=ParseMode.HTML)
        return
    
    try:
        user = update.effective_user
        msg = f"📩 <b>طلب جديد من مستخدم!</b>\n\n👤 الاسم: {user.first_name}\n🆔 الآيدي: `{user.id}`\n\n📖 الطلب: <b>{request_text}</b>"
        await context.bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.HTML)
        await update.message.reply_text("✅ <b>تم إرسال طلبك للإدارة.</b>", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("❌ حدث خطأ، حاول لاحقاً.")

# الاقتراح العشوائي
async def random_manga_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update, context): return
    conn = get_db()
    manga = conn.execute("SELECT id FROM mangas ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    
    if not manga:
        await update.callback_query.answer("📭 المكتبة فارغة!", show_alert=True)
    else:
        await show_manga_panel(update, context, manga['id'], 0, is_new=True)

# ====================================================================
#                           دالة العرض الرئيسية (Display Panel)
# ====================================================================

async def show_manga_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, manga_id, page=0, is_new=False, show_merged=False):
    conn = get_db()
    manga = conn.execute("SELECT * FROM mangas WHERE id = ?", (manga_id,)).fetchone()
    
    if not manga:
        conn.close()
        return

    # جلب الفصول (مدمجة أو عادية)
    is_m = 1 if show_merged else 0
    chapters = conn.execute("SELECT * FROM chapters WHERE manga_id = ? AND is_merged = ?", (manga_id, is_m)).fetchall()
    
    # الترتيب الذكي
    try:
        chapters.sort(key=lambda x: sort_key(x['chapter_number']))
    except: pass
    
    # حساب الصفحات
    limit = 15
    total_chapters = len(chapters)
    total_pages = math.ceil(total_chapters / limit)
    
    if total_pages == 0: total_pages = 1
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    # تقسيم الفصول
    current_page_chapters = chapters[page*limit : (page+1)*limit]
    
    # حالة المفضلة
    user_id = update.effective_user.id
    is_fav = conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND manga_id=?", (user_id, manga_id)).fetchone()
    conn.close()
    
    # النصوص
    prefix = "📦 <b>[فصول مدمجة]</b> " if show_merged else "⿻ ⦂ "
    caption = (
        f"{prefix}{html.escape(manga['title'])}\n"
        f" • الملفات المتوفرة ⦂ {total_chapters} 📚\n"
        f" • الحالة ⦂ {manga['status']}\n"
        f" • التقييم : {manga['rating']} ⭐.\n"
        f" • نُبذة ⦂ {html.escape(manga['description'][:800])}"
    )
    
    # الأزرار (Keyboard)
    keyboard = []
    
    # شبكة الفصول
    row = []
    for chap in current_page_chapters:
        btn_text = chap['chapter_number']
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"getchap_{chap['id']}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    # التنقل
    nav_buttons = []
    merged_suffix = "_m" if show_merged else "" # علامة لتمييز وضع المدمج في التنقل
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⏮️", callback_data=f"panel_{manga_id}_0{merged_suffix}"))
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"panel_{manga_id}_{page-1}{merged_suffix}"))
        
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"panel_{manga_id}_{page+1}{merged_suffix}"))
        nav_buttons.append(InlineKeyboardButton("⏭️", callback_data=f"panel_{manga_id}_{total_pages-1}{merged_suffix}"))
        
    if nav_buttons: keyboard.append(nav_buttons)
    
    # التحكم
    util_row = [InlineKeyboardButton("🔙 بحث جديد", callback_data="search_again")]
    
    if show_merged:
        util_row.append(InlineKeyboardButton("📄 الفصول الفردية", callback_data=f"panel_{manga_id}_0"))
    else:
        util_row.append(InlineKeyboardButton("📥 الفصول المدمجة", callback_data=f"panel_{manga_id}_0_m"))
        
    keyboard.append(util_row)
    
    # المفضلة والخروج
    if not show_merged:
        fav_text = "❌ حذف من المفضلة" if is_fav else "❤️ إضافة للمفضلة"
        fav_cb = f"fav_{manga_id}_remove" if is_fav else f"fav_{manga_id}_add"
        keyboard.append([InlineKeyboardButton(fav_text, callback_data=fav_cb)])
    
    keyboard.append([InlineKeyboardButton("↙️ العودة للقائمة", callback_data="back_start")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # الإرسال أو التعديل
    if is_new:
        await update.effective_chat.send_photo(
            photo=manga['photo_id'],
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        try:
            # نحاول تعديل الصورة (لو تغيرت المانجا)
            await update.callback_query.edit_message_media(
                media=InputMediaPhoto(media=manga['photo_id'], caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=reply_markup
            )
        except:
            # لو الصورة نفسها (تنقل صفحات) نعدل النص والزرار بس
            await update.callback_query.edit_message_caption(
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )

# ====================================================================
#                           المعالج (Brain)
# ====================================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # زر العودة مسموح للكل، الباقي يحتاج اشتراك
    if query.data != "back_start" and not await check_subscription(update, context):
        return

    user = query.from_user
    if check_user_status(user.id)['is_banned']:
        await query.answer("🚫", show_alert=True)
        return
    
    add_points(user.id, user.first_name)
    data = query.data
    
    # 1. العودة للبداية
    if data == "back_start":
        await start(update, context)
    
    # 2. عرض المانجا والتنقل
    elif data.startswith("panel_"):
        parts = data.split('_')
        mid = int(parts[1])
        pg = int(parts[2])
        is_merged_mode = (len(parts) > 3 and parts[3] == "m")
        
        # لو الرسالة الحالية نصية (جاي من بحث أو مفضلة)، نحذف ونرسل صورة
        is_new_msg = True if query.message.text else False
        
        if is_new_msg:
            await query.message.delete()
            await show_manga_panel(update, context, mid, pg, is_new=True, show_merged=is_merged_mode)
        else:
            await show_manga_panel(update, context, mid, pg, is_new=False, show_merged=is_merged_mode)

    # 3. إرسال ملف الفصل
    elif data.startswith("getchap_"):
        cid = int(data.split('_')[1])
        conn = get_db()
        chap = conn.execute("SELECT file_id, chapter_number FROM chapters WHERE id=?", (cid,)).fetchone()
        conn.close()
        
        await query.answer() # لإزالة التحميل
        await query.message.reply_document(document=chap['file_id'], caption=f"🍿 قراءة ممتعة! (فصل {chap['chapter_number']})")

    # 4. المفضلة (Add/Remove)
    elif data.startswith("fav_"):
        mid = int(data.split('_')[1])
        action = data.split('_')[2] # add or remove
        conn = get_db()
        
        if action == "add":
            conn.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (user.id, mid))
        else:
            conn.execute("DELETE FROM favorites WHERE user_id=? AND manga_id=?", (user.id, mid))
        
        conn.commit()
        conn.close()
        
        await query.answer("تم التحديث ✅")
        # تحديث الصفحة
        await show_manga_panel(update, context, mid, 0, is_new=False)

    # 5. عرض قائمة المفضلة
    elif data == "my_favs":
        conn = get_db()
        favs = conn.execute("SELECT m.id, m.title FROM mangas m JOIN favorites f ON m.id = f.manga_id WHERE f.user_id = ?", (user.id,)).fetchall()
        conn.close()
        
        if not favs:
            await query.answer("📭 قائمتك المفضلة فارغة", show_alert=True)
        else:
            keyboard = [[InlineKeyboardButton(f"📘 {m['title']}", callback_data=f"panel_{m['id']}_0")] for m in favs]
            keyboard.append([InlineKeyboardButton("🔙 العودة للقائمة", callback_data="back_start")])
            
            # تعديل حسب نوع الرسالة السابقة
            if query.message.photo:
                await query.message.delete()
                await query.message.reply_text("✨ <b>قائمتك المفضلة:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                await query.message.edit_text("✨ <b>قائمتك المفضلة:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

    # 6. المتصدرين
    elif data == "top_users":
        conn = get_db()
        tops = conn.execute("SELECT first_name, points FROM users ORDER BY points DESC LIMIT 10").fetchall()
        conn.close()
        
        msg = "🏆 <b>لائحة المتصدرين (الأكثر نشاطاً):</b>\n\n"
        for i, u in enumerate(tops, 1):
            name = html.escape(u['first_name'] or "User")
            msg += f"<b>{i}. {name}</b> ⇦ {u['points']} نقطة 🌟\n"
            
        keyboard = [[InlineKeyboardButton("🔙 العودة", callback_data="back_start")]]
        
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    
    # 7. البحث العشوائي
    elif data == "random_manga":
        await random_manga_func(update, context)

    # 8. زر إعادة البحث
    elif data == "search_again":
        await query.message.delete()
        await query.message.reply_text("🔎 <b>اكتب اسم المانجا الآن للبحث:</b>", parse_mode=ParseMode.HTML)


# ====================================================================
#                           التشغيل الرئيسي (Main)
# ====================================================================

def main():
    init_db()
    
    app = Application.builder().token(TOKEN).build()
    
    # مؤقت للمحادثات (عشان ما يعلقش)
    TIMEOUT = 600 # 10 دقائق
    
    # 1. إضافة مانجا
    conv_add = ConversationHandler(
        entry_points=[CommandHandler('add', admin_add_manga)],
        states={
            TITLE: [MessageHandler(filters.TEXT, get_title)],
            GENRE: [MessageHandler(filters.TEXT, get_genre)],
            STATUS: [MessageHandler(filters.TEXT, get_status)],
            RATING: [MessageHandler(filters.TEXT, get_rating)],
            DESC: [MessageHandler(filters.TEXT, get_desc)],
            PHOTO: [MessageHandler(filters.PHOTO, get_photo)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=TIMEOUT
    )
    
    # 2. أرشفة فصول
    conv_index = ConversationHandler(
        entry_points=[CommandHandler('index', start_indexing)],
        states={
            SELECT_MANGA_INDEX: [CallbackQueryHandler(select_manga_for_index, pattern="^selidx_")],
            CHOOSE_TYPE: [CallbackQueryHandler(choose_upload_type, pattern="^type_")],
            RECEIVE_FORWARDS: [MessageHandler(filters.Document.ALL, archive_forwarded_files), CommandHandler('done', finish_indexing)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=TIMEOUT
    )
    
    # 3. حذف مانجا
    conv_delete = ConversationHandler(
        entry_points=[CommandHandler('delete_manga', start_delete_manga)],
        states={DELETE_SELECT: [CallbackQueryHandler(confirm_delete_manga)]},
        fallbacks=[CallbackQueryHandler(confirm_delete_manga, pattern="cancel_del")],
        conversation_timeout=TIMEOUT
    )
    
    app.add_handler(conv_add)
    app.add_handler(conv_index)
    app.add_handler(conv_delete)
    
    # أوامر الأدمن
    app.add_handler(CommandHandler("adminhelp", admin_help))
    app.add_handler(CommandHandler("stats", bot_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("adminlist", admin_list)) # تمت إعادتها
    app.add_handler(CommandHandler("backup", send_backup))
    app.add_handler(CommandHandler("promote", promote_admin))
    app.add_handler(CommandHandler("demote", demote_admin))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # أوامر المستخدم
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("request", request_manga))
    app.add_handler(CommandHandler("search", smart_search))
    
    # التعامل مع الرسائل النصية (للبحث)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_search))
    
    # التعامل مع الأزرار
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("🚀 Bot V15 (Original Full Code) is Running...")
    app.run_polling()

if __name__ == "__main__":
    main()