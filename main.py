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

# ==============================================================================
#                               الإعدادات العامة
# ==============================================================================

# 🛑 [تنبيه هام] قم بتغيير هذه البيانات ببياناتك الخاصة
TOKEN = "8305359920:AAFQJAe0IqRtHBNhQXVDjLju5kHydN-lwZg"           # 👈 توكن البوت
OWNER_ID = 8211646341                      # 👈 آيدي المالك (أرقام فقط)
OWNER_USERNAME = "drvirus_6"          # 👈 اسم مستخدم المالك (بدون @)
CHANNEL_LINK = "https://t.me/MangaKingdom_AR" # 👈 رابط القناة العام
FORCE_CHANNEL_ID = -1003534146570         # 👈 آيدي القناة الرقمي للاشتراك الإجباري (-100...)

# اسم ملف قاعدة البيانات (يفضل عدم تغييره للحفاظ على الداتا)
DB_NAME = "manga_bot_v17_ultimate.db"

# إعداد السجل (Logging) لاكتشاف الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تعريف مراحل المحادثات (Conversation States)
(
    TITLE,              # انتظار اسم المانجا
    GENRE,              # انتظار التصنيف
    STATUS,             # انتظار الحالة
    RATING,             # انتظار التقييم
    DESC,               # انتظار الوصف
    PHOTO,              # انتظار الصورة
    SELECT_MANGA_INDEX, # انتظار اختيار مانجا للأرشفة
    CHOOSE_TYPE,        # انتظار نوع الملفات
    RECEIVE_FORWARDS,   # انتظار استقبال الملفات
    DELETE_SELECT       # انتظار اختيار الحذف
) = range(10)


# ==============================================================================
#                           دوال قاعدة البيانات
# ==============================================================================

def init_db():
    """
    تقوم هذه الدالة بإنشاء قاعدة البيانات والجداول الضرورية.
    تقوم أيضاً بعملية التحديث التلقائي إذا كانت الجداول قديمة.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 1. إنشاء جدول المستخدمين
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            first_name TEXT,
            points INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    ''')
    
    # محاولة إضافة أعمدة جديدة إذا لم تكن موجودة (تحديث الهيكل القديم)
    try:
        c.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    except sqlite3.OperationalError:
        pass

    # 2. إنشاء جدول المانجا
    c.execute('''
        CREATE TABLE IF NOT EXISTS mangas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            genre TEXT,
            status TEXT, 
            rating TEXT, 
            description TEXT, 
            photo_id TEXT
        )
    ''')
    
    # 3. إنشاء جدول الفصول (يدعم الفردي والمدمج)
    c.execute('''
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manga_id INTEGER,
            chapter_number TEXT,
            file_id TEXT,
            is_merged INTEGER DEFAULT 0,
            FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
        )
    ''')
                 
    # 4. إنشاء جدول المفضلة
    c.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER, 
            manga_id INTEGER
        )
    ''')
    
    # تسجيل المالك كأدمن وحصانته
    c.execute("INSERT OR IGNORE INTO users (user_id, is_admin, points) VALUES (?, 1, 999999)", (OWNER_ID,))
    c.execute("UPDATE users SET is_admin = 1, is_banned = 0 WHERE user_id = ?", (OWNER_ID,))
    
    conn.commit()
    conn.close()

def get_db():
    """تسهل عملية الاتصال واسترجاع البيانات كصفوف (Dictionary)."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# ==============================================================================
#                           أدوات مساعدة (Utility Functions)
# ==============================================================================

def check_user_status(user_id):
    """
    ترجع معلومات المستخدم (هل هو أدمن؟ هل هو محظور؟)
    """
    conn = get_db()
    user = conn.execute("SELECT is_admin, is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return {'is_admin': 0, 'is_banned': 0}
    return user

def add_points(user_id, first_name):
    """
    تضيف نقاط تفاعل للمستخدم ليظهر في المتصدرين.
    """
    conn = get_db()
    # تأكد من وجوده أولاً
    conn.execute("INSERT OR IGNORE INTO users (user_id, first_name, points) VALUES (?, ?, 0)", (user_id, first_name))
    # تحديث النقاط والاسم
    conn.execute("UPDATE users SET points = points + 1, first_name = ? WHERE user_id = ?", (first_name, user_id))
    conn.commit()
    conn.close()

def extract_chapter_number(text):
    """
    القلب النابض لاستخراج رقم الفصل بذكاء من اسم الملف.
    يحل مشاكل vinland saga والأسماء المعقدة.
    """
    if not text:
        return "0"
    
    # تنظيف النص
    text_clean = text.lower()
    
    # الأولوية 1: علامات الفصل الواضحة (ch, ep, #)
    markers = [
        r'ch(?:apter|ap|\.)?\s*[._-]?\s*(\d+(\.\d+)?)',  # ch102, chapter 102
        r'#\s*(\d+(\.\d+)?)',                             # #102
        r'ep\s*(\d+(\.\d+)?)'                             # ep102
    ]
    for pattern in markers:
        match = re.search(pattern, text_clean)
        if match:
            return match.group(1)
            
    # الأولوية 2: النطاقات (للفصول المدمجة مثل 0-20)
    range_match = re.search(r'(\d+\s*-\s*\d+)', text)
    if range_match: 
        return range_match.group(1).replace(" ", "")

    # الأولوية 3: كلمة Volume
    vol_match = re.search(r'vol(?:ume|\.)?\s*(\d+(\.\d+)?)', text_clean)
    if vol_match:
        return vol_match.group(1)
    
    # الأولوية 4: أي رقم عائم يظهر في النص
    num_match = re.search(r'(\d+(\.\d+)?)', text)
    if num_match: 
        return num_match.group(1)
    
    return "ملف" # إذا فشل كل شيء

def sort_key(text):
    """
    مساعد لترتيب الفصول بشكل طبيعي (1, 2, 10 بدلاً من 1, 10, 2).
    """
    nums = re.findall(r'\d+', text)
    if nums:
        return float(nums[0])
    return 0.0

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يتحقق ما إذا كان المستخدم مشتركاً في قناة البوت الإجبارية.
    """
    user_id = update.effective_user.id
    
    # استثناء الأدمن والمالك من الفحص
    status = check_user_status(user_id)
    if user_id == OWNER_ID or status['is_admin']:
        return True
    
    try:
        # فحص الحالة عبر API تيليجرام
        member = await context.bot.get_chat_member(chat_id=FORCE_CHANNEL_ID, user_id=user_id)
        
        # إذا غادر أو تم طرده
        if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
            raise Exception("User is not a member")
            
        return True
    
    except Exception as e:
        # رسالة الطلب
        msg = (
            "🚫 <b>عذراً عزيزي المستخدم!</b>\n\n"
            "يجب عليك الاشتراك في القناة الرسمية للبوت أولاً لتتمكن من استخدامه.\n"
            "اشترك ثم حاول مرة أخرى."
        )
        keyboard = [[InlineKeyboardButton("📢 اضغط هنا للاشتراك", url=CHANNEL_LINK)]]
        
        if update.callback_query:
            await update.callback_query.answer("⚠️ يجب الاشتراك أولاً!", show_alert=True)
            # اختيارياً، يمكن إرسال الرسالة كنص جديد
            # await update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            
        return False


# ==============================================================================
#                           أوامر لوحة التحكم (Admin Dashboard)
# ==============================================================================

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض قائمة الأوامر الخاصة بالأدمن فقط."""
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']:
        return # تجاهل
    
    help_text = (
        "👮‍♂️ <b>لوحة تحكم الأدمن المركزية (V17)</b>\n\n"
        "📥 <b>إدارة المحتوى:</b>\n"
        "• `/add` - إضافة مانجا جديدة للقاعدة\n"
        "• `/index` - رفع فصول لمانجا (فردي/مدمج)\n"
        "• `/delete_manga` - حذف عمل كامل مع فصوله\n\n"
        "👥 <b>إدارة الأعضاء:</b>\n"
        "• `/promote [ID]` - ترقية مستخدم لمشرف\n"
        "• `/demote [ID]` - تنزيل مشرف\n"
        "• `/ban [ID]` - حظر مستخدم\n"
        "• `/unban [ID]` - رفع الحظر\n"
        "• `/adminlist` - عرض المشرفين الحاليين\n\n"
        "📡 <b>النظام والنشر:</b>\n"
        "• `/broadcast [رسالة]` - إذاعة للكل\n"
        "• `/stats` - عرض الإحصائيات الكاملة\n"
        "• `/backup` - سحب نسخة احتياطية للداتا\n\n"
        "⚠️ <b>أوامر الطوارئ:</b>\n"
        "• `/cancel` - إيقاف أي عملية معلقة"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة بجميع الأدمنز المسجلين."""
    if update.effective_user.id != OWNER_ID:
        return

    conn = get_db()
    admins = conn.execute("SELECT user_id, first_name FROM users WHERE is_admin = 1").fetchall()
    conn.close()
    
    if not admins:
        msg = "لا يوجد أدمنز غيرك."
    else:
        msg = "👮‍♂️ <b>قائمة المشرفين (Admins):</b>\n\n"
        for admin in admins:
            name = html.escape(admin['first_name'] or "بدون اسم")
            msg += f"🔹 <b>{name}</b> ➣ <code>{admin['user_id']}</code>\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض إحصائيات دقيقة عن البوت."""
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']:
        return
    
    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    mangas_count = conn.execute("SELECT COUNT(*) FROM mangas").fetchone()[0]
    chapters_count = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
    banned_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
    admins_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]
    conn.close()
    
    msg = (
        "📊 <b>الإحصائيات المباشرة:</b>\n\n"
        f"👤 إجمالي الأعضاء: <b>{users_count}</b>\n"
        f"👮‍♂️ عدد المشرفين: <b>{admins_count}</b>\n"
        f"📚 عدد المانجات: <b>{mangas_count}</b>\n"
        f"📄 إجمالي الملفات: <b>{chapters_count}</b>\n"
        f"⛔️ المحظورين: <b>{banned_count}</b>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أداة الإذاعة الجماعية (Broadcast)."""
    if update.effective_user.id != OWNER_ID:
        return
    
    message_text = " ".join(context.args)
    if not message_text:
        await update.message.reply_text("⚠️ <b>خطأ:</b> الرجاء كتابة الرسالة بجانب الأمر.\nمثال: `/broadcast رمضان كريم`", parse_mode=ParseMode.HTML)
        return
    
    await update.message.reply_text(f"⏳ <b>بدأ الإرسال...</b>\nالنص: {html.escape(message_text)}", parse_mode=ParseMode.HTML)
    
    conn = get_db()
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    
    success = 0
    blocked = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user['user_id'], 
                text=f"📢 <b>بيان هام من الإدارة:</b>\n\n{message_text}", 
                parse_mode=ParseMode.HTML
            )
            success += 1
        except Exception:
            blocked += 1
            
    await update.message.reply_text(
        f"✅ <b>تم الانتهاء من الإذاعة.</b>\n\n"
        f"📤 وصلت بنجاح لـ: <b>{success}</b>\n"
        f"❌ فشل (حظر البوت): <b>{blocked}</b>",
        parse_mode=ParseMode.HTML
    )

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يقوم بإرسال ملف قاعدة البيانات كنسخة احتياطية."""
    if update.effective_user.id != OWNER_ID:
        return
    
    try:
        await update.message.reply_document(
            document=open(DB_NAME, 'rb'),
            caption=f"📦 <b>نسخة احتياطية كاملة</b>\n📅 {context.args}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ أثناء النسخ: {e}")

# --- دوال التحكم بالمستخدمين ---

async def promote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target_id = int(context.args[0])
        conn = get_db()
        # نضمن وجوده في الجدول
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_id,))
        conn.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم منح الصلاحيات للمستخدم `{target_id}`.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ خطأ! استخدم الأمر هكذا: `/promote [ID]`", parse_mode=ParseMode.HTML)

async def demote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target_id = int(context.args[0])
        conn = get_db()
        conn.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم سحب الصلاحيات من المستخدم `{target_id}`.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ خطأ! استخدم الأمر هكذا: `/demote [ID]`", parse_mode=ParseMode.HTML)

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']: return
    try:
        target_id = int(context.args[0])
        if target_id == OWNER_ID:
            await update.message.reply_text("⛔ لا يمكنك حظر المالك.")
            return
        
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_id,))
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🚫 تم حظر المستخدم `{target_id}` بنجاح.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ خطأ! استخدم الأمر هكذا: `/ban [ID]`", parse_mode=ParseMode.HTML)

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']: return
    try:
        target_id = int(context.args[0])
        conn = get_db()
        conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم رفع الحظر عن `{target_id}`.", parse_mode=ParseMode.HTML)
    except:
        await update.message.reply_text("⚠️ خطأ! استخدم الأمر هكذا: `/unban [ID]`", parse_mode=ParseMode.HTML)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة الطوارئ لإلغاء أي عملية."""
    await update.message.reply_text("❌ <b>تم إلغاء العملية الحالية.</b>\nيمكنك البدء من جديد.", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ==============================================================================
#                           المحادثات التفاعلية (Wizard Conversations)
# ==============================================================================

# --- 1. إضافة مانجا (Wizard) ---
async def admin_add_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']: 
        return ConversationHandler.END
        
    await update.message.reply_text("🆕 <b>بدء إضافة مانجا جديدة...</b>\n\n1️⃣ من فضلك أرسل <b>اسم المانجا</b>:", parse_mode=ParseMode.HTML)
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['title'] = update.message.text
    await update.message.reply_text("2️⃣ جيد، الآن أرسل <b>تصنيف المانجا</b> (مثلاً: أكشن، دراما):", parse_mode=ParseMode.HTML)
    return GENRE

async def get_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['genre'] = update.message.text
    await update.message.reply_text("3️⃣ ما هي <b>الحالة</b>؟ (مثلاً: مستمر، مكتمل):", parse_mode=ParseMode.HTML)
    return STATUS

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['status'] = update.message.text
    await update.message.reply_text("4️⃣ أرسل <b>التقييم</b> (مثلاً: 8.5/10):", parse_mode=ParseMode.HTML)
    return RATING

async def get_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['rating'] = update.message.text
    await update.message.reply_text("5️⃣ أرسل <b>نبذة أو وصف</b> عن القصة:", parse_mode=ParseMode.HTML)
    return DESC

async def get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['desc'] = update.message.text
    await update.message.reply_text("6️⃣ الخطوة الأخيرة: أرسل <b>صورة الغلاف</b>:", parse_mode=ParseMode.HTML)
    return PHOTO

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("⚠️ <b>يجب إرسال صورة (Image) وليست ملف!</b>\nحاول مرة أخرى:", parse_mode=ParseMode.HTML)
        return PHOTO
    
    photo_file = update.message.photo[-1].file_id
    data = context.user_data
    
    # الحفظ في قاعدة البيانات
    conn = get_db()
    conn.execute(
        "INSERT INTO mangas (title, genre, status, rating, description, photo_id) VALUES (?,?,?,?,?,?)",
        (data['title'], data['genre'], data['status'], data['rating'], data['desc'], photo_file)
    )
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"✅ <b>تمت الإضافة بنجاح!</b>\nالمانجا: {html.escape(data['title'])}\n\nيمكنك الآن إضافة الفصول عبر أمر /index.", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# --- 2. أرشفة الفصول (Indexing) ---
async def start_indexing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']: 
        return ConversationHandler.END
    
    conn = get_db()
    mangas = conn.execute("SELECT id, title FROM mangas").fetchall()
    conn.close()
    
    if not mangas:
        await update.message.reply_text("⚠️ <b>قاعدة البيانات فارغة!</b>\nقم بإضافة مانجا أولاً عبر /add.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    # إنشاء قائمة أزرار المانجا
    keyboard = []
    for manga in mangas:
        keyboard.append([InlineKeyboardButton(manga['title'], callback_data=f"selidx_{manga['id']}")])
        
    await update.message.reply_text("📂 <b>اختر المانجا التي تريد رفع فصول لها:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return SELECT_MANGA_INDEX

async def select_manga_for_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    manga_id = int(query.data.split('_')[1])
    context.user_data['index_manga_id'] = manga_id
    
    # سؤال عن نوع الملفات
    keyboard = [
        [InlineKeyboardButton("📄 فصول فردية (Single Chapters)", callback_data="type_normal")],
        [InlineKeyboardButton("📦 مجلدات مدمجة (Merged Volumes)", callback_data="type_merged")]
    ]
    await query.edit_message_text("⚙️ <b>ما نوع الملفات التي سترفعها؟</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return CHOOSE_TYPE

async def choose_upload_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    is_merged = 1 if query.data == "type_merged" else 0
    context.user_data['is_merged'] = is_merged
    type_name = "مدمجة" if is_merged else "فردية"
    
    await query.edit_message_text(
        f"⚡ <b>تم اختيار الوضع: {type_name}</b>\n\n"
        "1. اذهب لقناتك وحدد الملفات.\n"
        "2. قم بعمل <b>توجيه (Forward)</b> للملفات إلى هنا.\n"
        "3. سأقوم بالحفظ تلقائياً.\n"
        "4. عند الانتهاء اضغط /done.",
        parse_mode=ParseMode.HTML
    )
    return RECEIVE_FORWARDS

async def archive_forwarded_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # تجاهل الرسائل التي ليست مستندات
    if not update.message.document:
        return RECEIVE_FORWARDS
    
    manga_id = context.user_data['index_manga_id']
    is_merged = context.user_data['is_merged']
    document = update.message.document
    
    # دمج الاسم والوصف للبحث عن الرقم بدقة
    full_text_info = (document.file_name or "") + " " + (update.message.caption or "")
    
    # استخدام الدالة الذكية لاستخراج الرقم
    chapter_num_str = extract_chapter_number(full_text_info)
    
    conn = get_db()
    # التحقق من عدم التكرار (لتجنب التكرار في الأزرار)
    exists = conn.execute(
        "SELECT 1 FROM chapters WHERE manga_id=? AND file_id=? AND is_merged=?", 
        (manga_id, document.file_id, is_merged)
    ).fetchone()
    
    if not exists:
        conn.execute(
            "INSERT INTO chapters (manga_id, chapter_number, file_id, is_merged) VALUES (?, ?, ?, ?)",
            (manga_id, chapter_num_str, document.file_id, is_merged)
        )
        conn.commit()
        # إشعار سريع للمستخدم (Reply)
        await update.message.reply_text(f"📥 <b>تم استلام:</b> {chapter_num_str}", quote=True, parse_mode=ParseMode.HTML)
    
    conn.close()
    return RECEIVE_FORWARDS

async def finish_indexing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يتم استدعاؤها عند كتابة /done"""
    await update.message.reply_text("✅ <b>تم حفظ جميع الملفات المرسلة بنجاح.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# --- 3. حذف المانجا ---
async def start_delete_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = check_user_status(update.effective_user.id)
    if not status['is_admin']: 
        return ConversationHandler.END
        
    conn = get_db()
    mangas = conn.execute("SELECT id, title FROM mangas").fetchall()
    conn.close()
    
    if not mangas:
        await update.message.reply_text("📭 لا يوجد شيء لحذفه.")
        return ConversationHandler.END
    
    keyboard = []
    for manga in mangas:
        # زر الحذف يحمل اسم المانجا
        keyboard.append([InlineKeyboardButton(f"🗑 حذف: {manga['title']}", callback_data=f"del_{manga['id']}")])
        
    keyboard.append([InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_del")])
    
    await update.message.reply_text("⚠️ <b>قائمة الحذف:</b>\nاضغط على المانجا لحذفها نهائياً مع جميع فصولها.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return DELETE_SELECT

async def confirm_delete_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_del":
        await query.message.edit_text("✅ تم إلغاء عملية الحذف.")
        return ConversationHandler.END
        
    manga_id = int(query.data.split('_')[1])
    
    conn = get_db()
    # الحذف المتسلسل
    conn.execute("DELETE FROM favorites WHERE manga_id = ?", (manga_id,))
    conn.execute("DELETE FROM chapters WHERE manga_id = ?", (manga_id,))
    conn.execute("DELETE FROM mangas WHERE id = ?", (manga_id,))
    conn.commit()
    conn.close()
    
    await query.message.edit_text("🗑 <b>تم حذف المانجا وجميع بياناتها بنجاح.</b>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ==============================================================================
#                           أوامر وواجهة المستخدم (User Interface)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية للبوت."""
    user = update.effective_user
    
    # فحص الحظر
    if check_user_status(user.id)['is_banned']:
        return # تجاهل المحظور
    
    # إضافة نقاط تفاعل
    add_points(user.id, user.first_name)
    
    welcome_text = (
       "📚 <b>مرحبًا بك في بوت فهرس مملكة المانجا! (V5.0)</b>\n"
       "دليلك الشامل وأضخم نظام فهرسة لأعمال M3C 🔥\n"
       "نظمنا لك: مانجا / مانهوا / كوميكس بدقة عالية\n\n"
       "🔹 <b>الأوامر المتاحة:</b>\n"
       "<code>/search</code> - للبحث في الفهرس الملكي\n"
       "<code>/request</code> - لطلب إضافة عمل غير موجود\n\n"
       "🔗 تابع قناتنا الرئيسية: @MangaKingdom_AR\n"
       "⛔️ <b>الإصدار الفني - نظام الفهرسة الشامل</b>"
    ) 
    
    # أزرار القائمة الرئيسية
    keyboard = [
        [
            InlineKeyboardButton("✨ مفضلتي", callback_data="my_favs"),
            InlineKeyboardButton("🏆 المتصدرين", callback_data="top_users")
        ],
        [
            InlineKeyboardButton("🎲 اقترح لي عملاً عشوائياً", callback_data="random_manga")
        ],
        [
            InlineKeyboardButton("👨‍💻 مطور البوت", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("🪶 قناة الفهرس", url=CHANNEL_LINK)
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إذا كان الاستدعاء من زر (رجوع) نقوم بالتعديل
    if update.callback_query:
        # التعامل الذكي مع نوع الرسالة (صورة أو نص)
        if update.callback_query.message.photo:
            await update.callback_query.message.delete()
            await update.callback_query.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def request_manga(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نظام طلب المانجا."""
    if not await check_subscription(update, context): return
    if check_user_status(update.effective_user.id)['is_banned']: return
    
    req_text = " ".join(context.args)
    if not req_text:
        await update.message.reply_text("📝 <b>طريقة الطلب:</b>\nاكتب /request مسافة اسم المانجا.\nمثال: `/request Solo Leveling`", parse_mode=ParseMode.HTML)
        return
        
    try:
        # إرسال للمالك
        u = update.effective_user
        msg = f"📩 <b>طلب جديد من {u.first_name}:</b>\n🆔 `{u.id}`\n\n📖 الطلب: <b>{req_text}</b>"
        await context.bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode=ParseMode.HTML)
        
        await update.message.reply_text("✅ <b>تم استلام طلبك وإرساله للإدارة.</b> شكراً لك!", parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text("❌ حدث خطأ في إرسال الطلب.")

async def smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث الذكي."""
    if not await check_subscription(update, context): return
    if check_user_status(update.effective_user.id)['is_banned']: return
    
    query = ""
    # التحقق هل هو أمر /search أم نص عادي
    if context.args:
        query = " ".join(context.args)
    elif update.message and update.message.text and not update.message.text.startswith("/"):
        query = update.message.text
        
    if not query:
        await update.message.reply_text("🔍 اكتب اسم للبحث.")
        return
    
    # تسجيل نقاط
    add_points(update.effective_user.id, update.effective_user.first_name)
    
    conn = get_db()
    # بحث مرن
    results = conn.execute("SELECT id, title FROM mangas WHERE LOWER(title) LIKE ?", (f'%{query.lower()}%',)).fetchall()
    conn.close()
    
    if not results:
        await update.message.reply_text("❌ <b>عذراً، لم أجد نتائج مطابقة.</b>\nحاول كتابة جزء بسيط من الاسم.", parse_mode=ParseMode.HTML)
        return
        
    # إذا نتيجة واحدة
    if len(results) == 1:
        await show_manga_panel(update, context, results[0]['id'], 0, is_new=True)
    else:
        # عرض قائمة
        keyboard = []
        for m in results:
            keyboard.append([InlineKeyboardButton(f"📘 {m['title']}", callback_data=f"panel_{m['id']}_0")])
            
        await update.message.reply_text(f"🔎 <b>نتائج البحث عن '{html.escape(query)}':</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def random_manga_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جلب مانجا عشوائية."""
    if not await check_subscription(update, context): return
    
    conn = get_db()
    manga = conn.execute("SELECT id FROM mangas ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    
    if not manga:
        await update.callback_query.answer("⚠️ المكتبة فارغة!", show_alert=True)
    else:
        await show_manga_panel(update, context, manga['id'], 0, is_new=True)


# ==============================================================================
#                           محرك العرض (The Display Engine)
# ==============================================================================

async def show_manga_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, manga_id, page=0, is_new=False, show_merged=False):
    """
    الدالة الرئيسية المسؤولة عن عرض صورة المانجا والأزرار والتبديل بين الصفحات.
    """
    conn = get_db()
    manga = conn.execute("SELECT * FROM mangas WHERE id = ?", (manga_id,)).fetchone()
    
    if not manga:
        conn.close()
        return

    # تحديد نوع الفصول المطلوبة
    is_m_val = 1 if show_merged else 0
    chapters = conn.execute("SELECT * FROM chapters WHERE manga_id = ? AND is_merged = ?", (manga_id, is_m_val)).fetchall()
    
    # الترتيب باستخدام الدالة الذكية
    try:
        chapters.sort(key=lambda x: sort_key(x['chapter_number']))
    except:
        pass # التجاوز في حال الخطأ
    
    # حساب Pagination
    LIMIT_PER_PAGE = 15
    total_items = len(chapters)
    total_pages = math.ceil(total_items / LIMIT_PER_PAGE)
    
    if total_pages == 0: total_pages = 1
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start_idx = page * LIMIT_PER_PAGE
    end_idx = start_idx + LIMIT_PER_PAGE
    current_chapters = chapters[start_idx:end_idx]
    
    # فحص المفضلة
    user_id = update.effective_user.id
    is_fav = conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND manga_id=?", (user_id, manga_id)).fetchone()
    conn.close()
    
    # تحضير النص
    prefix_icon = "📦 <b>[نسخة مدمجة]</b> " if show_merged else "⿻ ⦂ "
    caption_text = (
        f"{prefix_icon}{html.escape(manga['title'])}\n"
        f" • عدد الملفات ⦂ {total_items} 📚\n"
        f" • الحالة ⦂ {manga['status']}\n"
        f" • التقييم : {manga['rating']} ⭐.\n"
        f" • نُبذة عن العمل ⦂ {html.escape(manga['description'][:800])}"
    )
    
    # بناء الأزرار
    keyboard = []
    
    # أزرار الفصول (شبكة 5 أعمدة)
    row = []
    for chap in current_chapters:
        btn_text = chap['chapter_number']
        # إذا النص طويل جداً نقصره للزر
        if len(btn_text) > 8:
            btn_text = btn_text[:8]
            
        row.append(InlineKeyboardButton(btn_text, callback_data=f"getchap_{chap['id']}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    # أزرار التنقل بين الصفحات
    nav_row = []
    suffix_merged = "_m" if show_merged else ""
    
    if page > 0:
        nav_row.append(InlineKeyboardButton("⏮️", callback_data=f"panel_{manga_id}_0{suffix_merged}"))
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"panel_{manga_id}_{page-1}{suffix_merged}"))
    
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"panel_{manga_id}_{page+1}{suffix_merged}"))
        nav_row.append(InlineKeyboardButton("⏭️", callback_data=f"panel_{manga_id}_{total_pages-1}{suffix_merged}"))
        
    if nav_row: keyboard.append(nav_row)
    
    # أزرار الخدمات
    utils_row = [InlineKeyboardButton("🔙 بحث جديد", callback_data="search_again")]
    
    # زر التبديل
    if show_merged:
        utils_row.append(InlineKeyboardButton("📄 الفصول الفردية", callback_data=f"panel_{manga_id}_0"))
    else:
        utils_row.append(InlineKeyboardButton("📥 الفصول المدمجة", callback_data=f"panel_{manga_id}_0_m"))
        
    keyboard.append(utils_row)
    
    # أزرار المفضلة والخروج
    if not show_merged:
        fav_icon = "❌ حذف من المفضلة" if is_fav else "❤️ إضافة للمفضلة"
        fav_cb = f"fav_{manga_id}_rem" if is_fav else f"fav_{manga_id}_add"
        keyboard.append([InlineKeyboardButton(fav_icon, callback_data=fav_cb)])
        
    keyboard.append([InlineKeyboardButton("↙️ العودة الى القائمة", callback_data="back_start")])
    
    markup = InlineKeyboardMarkup(keyboard)
    
    # طريقة الإرسال (جديد أم تعديل)
    if is_new:
        await update.effective_chat.send_photo(
            photo=manga['photo_id'],
            caption=caption_text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
    else:
        # نحاول تعديل الرسالة الحالية لتجنب الوميض
        try:
            await update.callback_query.edit_message_media(
                media=InputMediaPhoto(media=manga['photo_id'], caption=caption_text, parse_mode=ParseMode.HTML),
                reply_markup=markup
            )
        except:
            # في حال لم تتغير الصورة، نعدل النص والأزرار فقط
            try:
                await update.callback_query.edit_message_caption(
                    caption=caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
            except:
                pass # لا يوجد تغيير

# ==============================================================================
#                           معالج الـ Callback (ضغط الأزرار)
# ==============================================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    # التحقق من الحظر
    if check_user_status(user_id)['is_banned']:
        await query.answer("🚫", show_alert=True)
        return

    # زر الخروج لا يحتاج اشتراك، الباقي يحتاج
    if data != "back_start" and not await check_subscription(update, context):
        return
        
    # تسجيل نقطة تفاعل
    add_points(user_id, query.from_user.first_name)
    
    # --- التوجيه ---
    
    if data == "back_start":
        await start(update, context)
        
    elif data == "search_again":
        await query.message.delete()
        await query.message.reply_text("🔍 <b>البحث:</b> اكتب اسم المانجا الآن.", parse_mode=ParseMode.HTML)
        
    elif data == "random_manga":
        await random_manga_func(update, context)
        
    # المفضلة
    elif data == "my_favs":
        conn = get_db()
        favs = conn.execute("SELECT m.id, m.title FROM mangas m JOIN favorites f ON m.id = f.manga_id WHERE f.user_id = ?", (user_id,)).fetchall()
        conn.close()
        
        if not favs:
            await query.answer("📭 ليس لديك مانجات مفضلة!", show_alert=True)
        else:
            kb = []
            for f in favs:
                kb.append([InlineKeyboardButton(f"📘 {f['title']}", callback_data=f"panel_{f['id']}_0")])
            kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_start")])
            
            if query.message.photo:
                await query.message.delete()
                await query.message.reply_text("✨ <b>قائمتك المفضلة:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            else:
                await query.message.edit_text("✨ <b>قائمتك المفضلة:</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    # المتصدرين
    elif data == "top_users":
        conn = get_db()
        top_list = conn.execute("SELECT first_name, points FROM users ORDER BY points DESC LIMIT 10").fetchall()
        conn.close()
        
        msg = "🏆 <b>قائمة المتصدرين (الأكثر تفاعلاً):</b>\n\n"
        for idx, u in enumerate(top_list, 1):
            u_name = html.escape(u['first_name'] or "Unknown")
            msg += f"{idx}. <b>{u_name}</b> ⇦ {u['points']} نقطة 🌟\n"
            
        kb = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_start")]]
        
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    # إضافة/حذف مفضلة
    elif data.startswith("fav_"):
        parts = data.split('_')
        manga_id = int(parts[1])
        action = parts[2] # add or rem
        
        conn = get_db()
        if action == "add":
            conn.execute("INSERT OR IGNORE INTO favorites (user_id, manga_id) VALUES (?, ?)", (user_id, manga_id))
        else:
            conn.execute("DELETE FROM favorites WHERE user_id=? AND manga_id=?", (user_id, manga_id))
        conn.commit()
        conn.close()
        
        await query.answer("✅ تم التحديث")
        # تحديث اللوحة ليعكس حالة الزر
        await show_manga_panel(update, context, manga_id, 0, is_new=False, show_merged=False)

    # تحميل فصل
    elif data.startswith("getchap_"):
        cid = int(data.split('_')[1])
        conn = get_db()
        file_data = conn.execute("SELECT file_id, chapter_number FROM chapters WHERE id=?", (cid,)).fetchone()
        conn.close()
        
        if file_data:
            await query.answer("جاري الإرسال...")
            await query.message.reply_document(
                document=file_data['file_id'], 
                caption=f"🍿 قراءة ممتعة - <b>{file_data['chapter_number']}</b>",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.answer("❌ الملف غير موجود", show_alert=True)

    # التنقل في اللوحة
    elif data.startswith("panel_"):
        # Format: panel_ID_Page(_m)
        parts = data.split('_')
        mid = int(parts[1])
        pg = int(parts[2])
        merged = (len(parts) > 3 and parts[3] == "m")
        
        # لو القائمة نصية (جاي من بحث أو مفضلة) نعتبرها New Message
        is_txt = bool(query.message.text)
        
        if is_txt:
            await query.message.delete()
            await show_manga_panel(update, context, mid, pg, is_new=True, show_merged=merged)
        else:
            await show_manga_panel(update, context, mid, pg, is_new=False, show_merged=merged)


# ==============================================================================
#                           المحرك الرئيسي (Main Runner)
# ==============================================================================

def main():
    """نقطة انطلاق البوت."""
    
    # 1. تجهيز القاعدة
    init_db()
    
    # 2. بناء التطبيق
    app = Application.builder().token(TOKEN).build()
    
    # إعداد Timeout للمحادثات (15 دقيقة) لمنع التعليق
    CONV_TIMEOUT = 900
    
    # --- محادثة إضافة مانجا ---
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
        conversation_timeout=CONV_TIMEOUT
    )
    
    # --- محادثة أرشفة الفصول ---
    conv_index = ConversationHandler(
        entry_points=[CommandHandler('index', start_indexing)],
        states={
            SELECT_MANGA_INDEX: [CallbackQueryHandler(select_manga_for_index, pattern="^selidx_")],
            CHOOSE_TYPE: [CallbackQueryHandler(choose_upload_type, pattern="^type_")],
            RECEIVE_FORWARDS: [
                MessageHandler(filters.Document.ALL, archive_forwarded_files), 
                CommandHandler('done', finish_indexing) # هنا الحل لمشكلة Done
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=CONV_TIMEOUT
    )
    
    # --- محادثة حذف مانجا ---
    conv_delete = ConversationHandler(
        entry_points=[CommandHandler('delete_manga', start_delete_manga)],
        states={
            DELETE_SELECT: [CallbackQueryHandler(confirm_delete_manga)]
        },
        fallbacks=[CallbackQueryHandler(confirm_delete_manga, pattern="cancel_del"), CommandHandler('cancel', cancel)],
        conversation_timeout=CONV_TIMEOUT
    )
    
    # تسجيل المحادثات
    app.add_handler(conv_add)
    app.add_handler(conv_index)
    app.add_handler(conv_delete)
    
    # تسجيل الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("adminhelp", admin_help))
    app.add_handler(CommandHandler("stats", bot_stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("backup", send_backup))
    app.add_handler(CommandHandler("adminlist", admin_list))
    app.add_handler(CommandHandler("promote", promote_admin))
    app.add_handler(CommandHandler("demote", demote_admin))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("request", request_manga))
    app.add_handler(CommandHandler("search", smart_search))
    app.add_handler(CommandHandler("cancel", cancel)) # أمر هام للخروج من أي تعليق
    
    # التعامل مع الرسائل النصية (للبحث السريع)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_search))
    
    # التعامل مع الأزرار
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("✅ Bot V17 (Ultimate & Expanded) is running successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
