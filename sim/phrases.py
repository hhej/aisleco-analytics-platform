"""Chat text for the simulated couples and staff (Spec 06 §6). Thai and English templates per intent,
with slots. The intent a couple expresses depends on their hidden readiness and stage (engine.py), so the
words carry signal the way real chats do. Slots: {date} {date_en} {guests} {budget} {pkg} {venue}."""

GREETING = {
    "th": ["สวัสดีค่ะ", "สวัสดีครับ", "สวัสดีค่า", "หวัดดีค่ะ แอดมิน", "สอบถามหน่อยค่ะ", "รบกวนสอบถามครับ", "สวัสดีค่ะ ทักมาจากติ๊กต็อกค่ะ", ""],
    "en": ["Hi!", "Hello", "Hi there,", "Good evening", "Hi, quick question", ""],
}
INTENT = {
    "ask_price": {
        "th": ["แพ็กเกจ{pkg}ราคาเท่าไหร่คะ", "ขอราคาแพ็กเกจหน่อยค่ะ", "ค่าใช้จ่ายประมาณเท่าไหร่ครับ", "ราคาเริ่มต้นเท่าไหร่คะ",
               "ขอเรทราคาหน่อยค่ะ", "จัดงาน {guests} คน ราคาประมาณเท่าไหร่คะ", "มีโปรไหมคะ ราคาเท่าไหร่", "ขอใบเสนอราคาได้ไหมคะ"],
        "en": ["How much is {pkg}?", "What's the price for {guests} guests?", "Could you send me your rates?", "How much does it cost roughly?",
               "Any price list?"],
    },
    "ask_availability": {
        "th": ["วันที่ {date} ยังว่างไหมคะ", "คิววันที่ {date} ว่างไหมครับ", "{date} รับงานไหมคะ", "อยากได้วันที่ {date} ค่ะ ยังทันไหม",
               "เช็กคิว {date} ให้หน่อยค่ะ"],
        "en": ["Is {date_en} still available?", "Are you free on {date_en}?", "Do you still have {date_en} open?"],
    },
    "mention_guests": {
        "th": ["แขกประมาณ {guests} คนค่ะ", "น่าจะ {guests} ท่านครับ", "แขกราวๆ {guests} คน"],
        "en": ["About {guests} guests.", "Around {guests} pax.", "We expect {guests} guests."],
    },
    "ask_package_detail": {
        "th": ["แพ็กเกจ{pkg}รวมอะไรบ้างคะ", "Full Planning มีอะไรบ้างคะ", "รวมดอกไม้กับช่างภาพไหมคะ", "ต่างกันยังไงระหว่าง Full กับ Partial คะ"],
        "en": ["What's included in {pkg}?", "Does it include flowers and the photographer?", "What's the difference between Full and Partial?"],
    },
    "ask_meeting": {
        "th": ["ขอนัดเข้าไปคุยที่ออฟฟิศได้ไหมคะ", "เสาร์นี้สะดวกนัดคุยไหมครับ", "อยากนัดปรึกษารายละเอียดค่ะ", "นัดวิดีโอคอลได้ไหมคะ"],
        "en": ["Can we book a consultation this weekend?", "Could we meet at your office?", "Can we set up a call to discuss?"],
    },
    "price_objection": {
        "th": ["แพงไปนิดนึงค่ะ", "เกินงบไปหน่อยครับ", "ลดได้ไหมคะ", "งบเรามี {budget} เองค่ะ", "ขอราคาพิเศษได้ไหมคะ"],
        "en": ["That's a bit over our budget.", "Is there any discount?", "Too expensive for us, sorry."],
    },
    "family_decision": {
        "th": ["ขอปรึกษาที่บ้านก่อนนะคะ", "ขอถามผู้ใหญ่ก่อนค่ะ", "เดี๋ยวขอคุยกับแฟนก่อนครับ", "ขอเวลาคิดแป๊บนะคะ"],
        "en": ["Let me discuss with my family first.", "I'll check with my fiancé and get back to you."],
    },
    "competitor": {
        "th": ["กำลังเทียบกับที่อื่นอยู่ค่ะ", "ที่อื่นให้ราคาถูกกว่านิดนึง", "ขอดูหลายๆ ที่ก่อนนะคะ"],
        "en": ["We're comparing a few planners.", "Another planner quoted us less."],
    },
    "positive": {
        "th": ["ขอบคุณมากค่ะ", "ชอบมากเลยค่ะ", "สวยมากค่ะ ตรงกับที่อยากได้", "โอเคเลยครับ", "ขอบคุณที่ตอบไวนะคะ 🙏"],
        "en": ["Thank you so much!", "Love it!", "Perfect, thanks.", "Great, that helps a lot."],
    },
    "commit": {
        "th": ["ตกลงค่ะ จองเลย", "โอนมัดจำได้ที่ไหนคะ", "พร้อมเซ็นสัญญาแล้วค่ะ", "ขอเลขบัญชีสำหรับมัดจำหน่อยครับ"],
        "en": ["Let's book it.", "Where do we send the deposit?", "We're ready to sign."],
    },
    "smalltalk": {
        "th": ["ค่ะ", "โอเคค่ะ", "รับทราบค่ะ", "ได้ครับ", "👍", "ขอบคุณค่ะ"],
        "en": ["ok", "Noted.", "Sure", "👍"],
    },
}
VENUES = ["โรงแรม", "สวน", "ริมแม่น้ำ", "hotel ballroom", "garden", "บ้าน"]
STAFF = {
    "th": ["สวัสดีค่ะ ขอบคุณที่สนใจ Aisle & Co. นะคะ ส่งรายละเอียดแพ็กเกจให้ค่ะ", "ได้เลยค่ะ เดี๋ยวเช็กคิวให้นะคะ",
           "สะดวกนัดคุยวันไหนดีคะ", "ส่งใบเสนอราคาให้แล้วนะคะ", "สอบถามเพิ่มเติมได้ตลอดเลยค่ะ", "มีโปรสำหรับงานช่วงนี้ด้วยค่ะ",
           "ไม่ทราบว่าตัดสินใจได้หรือยังคะ", "ยินดีด้วยนะคะ 💐"],
    "en": ["Thanks for reaching out! Here is our package sheet.", "Let me check that date for you.",
           "When would suit you for a consultation?", "The quote is attached.", "Just checking in, any questions?"],
}
BOT = "ขอบคุณที่ทักมานะคะ แอดมินจะตอบกลับในเวลาทำการ 09:00–20:00 ค่ะ"
TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
NICKNAMES = ["Ploy", "Mint", "Nok", "Fah", "Kwan", "Pim", "Bow", "Ice", "Praew", "Fern", "May", "Beam", "Jay", "Ton", "Bank",
             "Golf", "Toey", "Pond", "Earth", "Nut", "Aom", "Gift", "View", "Mild", "Tarn", "Jane", "Kate", "Opal", "Ink", "Bam"]
