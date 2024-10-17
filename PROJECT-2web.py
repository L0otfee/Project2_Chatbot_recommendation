import sys
import re
import os
import json
import requests
from flask import Flask, request, jsonify
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
from neo4j import GraphDatabase
from linebot import LineBotApi
from linebot.models import TextSendMessage, QuickReply, QuickReplyButton, MessageAction, FlexSendMessage
import logging
from linebot.models import ImageSendMessage, FlexSendMessage, TextSendMessage
import random

# Neo4j settings
URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "Lutfee2salaeh")
neo4j_driver = GraphDatabase.driver(URI, auth=AUTH)

# LINE Bot settings
LINE_CHANNEL_ACCESS_TOKEN = 'nL/4gDUGSi4jaxMVsZZKh22TNvQ70SSgIAkzHgZAYg3zSNs1Dq2yJYuUMxa6AQRRCpd5YMh+oakz1+r17s7R3EV3MSqHdme1VhXN2EYtQBjpXgZ6+PDBxYKhMNZJR6n/y6r6GRcwYW0f/HH9mEmuxwdB04t89/1O/w1cDnyilFU='
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

# Ollama API settings
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Use SentenceTransformer model
model = SentenceTransformer('sentence-transformers/distiluse-base-multilingual-cased-v2')

# FAISS index setup
corpus = []
responses = {}
node_types = {}
vector_dimension = 0
index = None

# Flask app initialization
app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Fetch data from Neo4j
def fetch_data_from_neo4j():
    global corpus, responses, node_types, vector_dimension, index
    try:
        with neo4j_driver.session() as session:
            query = """
            MATCH (n)
            WHERE n:Category OR n:Product OR n:Promotion OR n:Greeting
            RETURN n
            """
            results = session.run(query)

            corpus = []
            responses = {}
            node_types = {}

            for record in results:
                node = record["n"]
                node_type = list(node.labels)[0]

                if node_type == 'Promotion':
                    title = node.get("title", "")
                    details = node.get("details", "")
                    if title and details:
                        corpus.append(title)
                        responses[title] = details
                        node_types[title] = node_type
                else:
                    name = node.get("name", "")
                    reply = node.get("msg_reply", "")
                    if name and reply:
                        corpus.append(name)
                        responses[name] = reply
                        node_types[name] = node_type

            if not corpus:
                logging.warning("No data loaded from Neo4j.")
                return

            # Index FAISS
            corpus_vec = model.encode(corpus, convert_to_tensor=True, normalize_embeddings=True).cpu().numpy()
            vector_dimension = corpus_vec.shape[1]
            index = faiss.IndexFlatL2(vector_dimension)
            index.add(np.array(corpus_vec).astype('float32'))
            logging.info(f"FAISS index created successfully with {len(corpus)} items")

    except Exception as e:
        logging.error(f"Error in fetch_data_from_neo4j: {str(e)}")
        raise

# Call this function to load data
fetch_data_from_neo4j()

def ollama_new(msg):
    instruction = (
        f"คุณเป็น AI พนักงานร้าน Starbucks "
        f"กรุณาเรียงลำดับคำใหม่จากข้อความนี้: \"{msg}\" \n"
        f"ตอบเป็นภาษาไทย กระชับ ตรงประเด็น ไม่เกิน 2 ประโยค"
    )

    payload = {
        "model": "llama3.2",
        "prompt": instruction,
        "max_tokens": 30,
        "stream": False
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(OLLAMA_API_URL, headers=headers, json=payload)
        response.raise_for_status()  # Raise an error for 4xx/5xx responses
        data = response.json()
        return data.get("response", "ไม่สามารถรับข้อมูลจาก Ollama ได้")
    except requests.exceptions.RequestException as e:
        logging.error(f"Ollama API error: {str(e)}")
        return "ไม่สามารถติดต่อกับ API ได้ในขณะนี้"
    

def query_starbucks_ollama(prompt):
    instruction = (
        f"คุณเป็น AI ผู้เชี่ยวชาญเกี่ยวกับ Starbucks ในประเทศไทย "
        f"กรุณาตอบคำถามต่อไปนี้เกี่ยวกับ Starbucks: {prompt}\n"
        f"ตอบเป็นภาษาไทย กระชับ ตรงประเด็น ไม่เกิน 2 ประโยค"
    )

    payload = {
        "model": "supachai/llama-3-typhoon-v1.5",
        "prompt": instruction,
        "max_tokens": 30,
        "stream": False
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(OLLAMA_API_URL, headers=headers, json=payload)
        response.raise_for_status()  # Raise an error for 4xx/5xx responses
        data = response.json()
        return data.get("response", "ไม่สามารถรับข้อมูลจาก Ollama ได้")
    except requests.exceptions.RequestException as e:
        logging.error(f"Ollama API error: {str(e)}")
        return "ไม่สามารถติดต่อกับ API ได้ในขณะนี้"

def create_starbucks_quick_reply():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="เมนูแนะนำ", text="เมนูยอดนิยมของ Starbucks")),
        QuickReplyButton(action=MessageAction(label="โปรโมชั่น", text="โปรโมชั่นล่าสุดของ Starbucks")),
    ])

def get_product_from_neo4j(product_title):
    with neo4j_driver.session() as session:
        cypher_query = '''
        MATCH (p:Product)
        WHERE p.title = $title
        RETURN p.product_url as product_url, p.image_url as image_url, 
               p.price as price, p.detail as detail, p.title as title
        LIMIT 1
        '''
        result = session.run(cypher_query, title=product_title).single()
        return dict(result) if result else None

def get_greeting_from_neo4j():
    with neo4j_driver.session() as session:
        query = """
        MATCH (g:Greeting)
        RETURN g.msg_reply as greeting
        ORDER BY RAND()
        LIMIT 1
        """
        result = session.run(query).single()
        return result['greeting'] if result else "สวัสดีครับ ยินดีต้อนรับสู่ Starbucks Thailand!"

def is_greeting(message):
    greetings = ['สวัสดี', 'หวัดดี', 'ดี', 'hi', 'hello', 'hey']
    return any(greeting in message.lower() for greeting in greetings)

def get_top_similar_products(user_message, k=3):
    try:
        user_vector = model.encode([user_message], convert_to_tensor=True, normalize_embeddings=True).cpu().numpy()
        D, I = index.search(user_vector.astype('float32'), k=k)
        results = []
        for i in range(k):
            if i < len(I[0]):
                product_title = corpus[I[0][i]]
                similarity = 1 - D[0][i]  # Convert distance to similarity
                results.append((product_title, similarity))
        return results
    except Exception as e:
        logging.error(f"Error in FAISS search: {str(e)}")
        return []

def get_similar_products(user_message, threshold=0.5, top_k=3):
    try:
        # คำนวณเวกเตอร์จากข้อความผู้ใช้
        user_vector = model.encode([user_message], convert_to_tensor=True, normalize_embeddings=True).cpu().numpy()

        # ค้นหาข้อความใน corpus ที่คล้ายกับข้อความผู้ใช้ที่สุด
        D, I = index.search(user_vector.astype('float32'), k=top_k)

        # รวบรวมสินค้าที่มีความคล้ายกันเกิน threshold
        similar_products = []
        for i in range(len(D[0])):
            similarity = 1 - D[0][i]  # คำนวณ similarity จากระยะทาง L2
            if similarity >= threshold:
                product_title = corpus[I[0][i]]
                similar_products.append({
                    "title": product_title,
                    "similarity": similarity,
                    "details": responses[product_title]
                })

        return similar_products if similar_products else None
    except Exception as e:
        logging.error(f"Error finding similar products: {str(e)}")
        return None

def get_promotion_data():
    with neo4j_driver.session() as session:
        result = session.run("MATCH (p:Promotion) RETURN p.image_url, p.link, p.details, p.title LIMIT 1")
        promotion_data = result.single()
        if promotion_data:
            return {
                "image_url": promotion_data[0],
                "link": promotion_data[1],
                "details": promotion_data[2],
                "title": promotion_data[3]
            }
        return None

from linebot.models import FlexSendMessage

from neo4j import GraphDatabase
from linebot.models import FlexSendMessage

# เชื่อมต่อกับฐานข้อมูล Neo4j
uri = "bolt://localhost:7687"  # ปรับตามการตั้งค่าของคุณ
username = "neo4j"  # ชื่อผู้ใช้งาน (default: neo4j)
password = "Lutfee2salaeh"  # รหัสผ่านที่คุณตั้งไว้ใน Neo4j

driver = GraphDatabase.driver(uri, auth=(username, password))

# ฟังก์ชันเพื่อสร้าง Flex Message สำหรับสินค้า
def create_flex_message_for_products(products):
    bubbles = []
    seen_titles = set()  # ใช้ set เพื่อตรวจสอบว่าสินค้านี้ถูกเพิ่มไปแล้วหรือไม่

    # จำกัดจำนวนสินค้าที่จะแสดงไม่เกิน 12 รายการ
    for product in products[:12]:
        title = product.get("title", None)
        print("ข้อมูลสินค้า:", product)  # พิมพ์ข้อมูลเพื่อช่วยในการตรวจสอบ

        # ตรวจสอบว่าชื่อสินค้านี้ยังไม่ถูกเพิ่มไปใน bubbles
        if title and title not in seen_titles:
            seen_titles.add(title)  # บันทึกชื่อสินค้าไว้ใน set เพื่อป้องกันการแสดงซ้ำ
            product_url = product.get("product_url", "https://www.starbucks.co.th/")
            image_url = product.get("image_url", "https://via.placeholder.com/1024")
            details = product.get("detail", None)

            price_single = product.get("price", None)  # ฟิลด์ราคาเดียว
            price_grande = product.get("priceGrande", None)  # ราคา Grande
            price_tall = product.get("priceTall", None)  # ราคา Tall
            price_venti = product.get("priceVenti", None)  # ราคา Venti

            # แสดงข้อมูลราคาทั้งหมดเพื่อช่วยในการตรวจสอบ
            print(f"Price (Single): {price_single}")
            print(f"Price Tall: {price_tall}")
            print(f"Price Grande: {price_grande}")
            print(f"Price Venti: {price_venti}")
            print("-------------------------")

            # สร้างรายการเนื้อหาใน body
            body_contents = []
            if title:
                body_contents.append({
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "xl",
                    "wrap": True
                })
            if details:
                body_contents.append({
                    "type": "text",
                    "text": details,
                    "wrap": True,
                    "margin": "md",
                    "size": "sm"
                })

            # ตรวจสอบว่ามีข้อมูลราคาแบบเดียวหรือไม่
            if price_single:
                # แสดงราคาแบบเดียว
                body_contents.append({
                    "type": "text",
                    "text": f"{price_single}",
                    "wrap": True,
                    "margin": "md",
                    "size": "sm",
                    "color": "#2e2e20"
                })

            # แสดงราคาหลายขนาดถ้ามีข้อมูล
            price_text = ""
            if price_tall:
                price_text += f"Tall : {price_tall}\n"
            if price_grande:
                price_text += f"Grande : {price_grande}\n"
            if price_venti:
                price_text += f"Venti : {price_venti}\n"

            if price_text:
                body_contents.append({
                    "type": "text",
                    "text": price_text.strip(),
                    "wrap": True,
                    "margin": "md",
                    "size": "sm",
                    "color": "#2e2e20"
                })

            # สร้าง bubble สำหรับสินค้า
            bubble = {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": image_url,
                    "size": "full",
                    "aspectRatio": "20:13",
                    "aspectMode": "cover",
                    "action": {
                        "type": "uri",
                        "uri": product_url
                    }
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": body_contents
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "style": "link",
                            "height": "sm",
                            "action": {
                                "type": "uri",
                                "label": "ดูรายละเอียดเพิ่มเติม",
                                "uri": product_url
                            }
                        }
                    ]
                }
            }

            bubbles.append(bubble)

    # สร้าง Flex Message แบบ carousel
    flex_message = FlexSendMessage(
        alt_text='รายละเอียดสินค้าจาก Starbucks',
        contents={
            "type": "carousel",
            "contents": bubbles  # ใส่ bubbles ที่สร้างไว้
        }
    )

    return flex_message

# เชื่อมต่อกับฐานข้อมูล Neo4j
uri = "bolt://localhost:7687"
username = "neo4j"
password = "Lutfee2salaeh"
driver = GraphDatabase.driver(uri, auth=(username, password))

# ฟังก์ชันเพื่อดึงข้อมูลโปรโมชั่นทั้งหมด
def get_promotion_data():
    with driver.session() as session:
        result = session.run("MATCH (n:Promotion) RETURN n LIMIT 25")
        promotions = []
        for record in result:
            node = record["n"]
            promotions.append({
                "title": node.get("title"),
                "promotion_url": node.get("promotion_url"),
                "image_url": node.get("image"),
                "detail": node.get("details"),
                "expiry_date": node.get("date")
            })
        return promotions

# ฟังก์ชันเพื่อสร้าง Flex Message สำหรับโปรโมชั่น
def create_flex_message_for_promotions(promotions):
    bubbles = []
    seen_titles = set()  # ใช้ set เพื่อตรวจสอบว่าโปรโมชั่นนี้ถูกเพิ่มไปแล้วหรือไม่

    # จำกัดจำนวนโปรโมชั่นที่จะแสดงไม่เกิน 12 รายการ
    if promotions is None or not isinstance(promotions, list):
        return None
    
    for promotion in promotions[:12]:
        if not isinstance(promotion, dict):
            continue
        
        title = promotion.get("title", "ไม่มีชื่อโปรโมชั่น")
        promotion_url = promotion.get("promotion_url", "https://www.starbucks.co.th/promotions")  # ตั้งค่า URL เริ่มต้นถ้าไม่มีข้อมูล
        image_url = promotion.get("image_url", "https://via.placeholder.com/1024")  # ตั้งค่า URL ของรูปภาพถ้าไม่มีข้อมูล
        details = promotion.get("detail", "ไม่มีรายละเอียดเพิ่มเติม")
        expiry_date = promotion.get("expiry_date", "ไม่ระบุวันหมดอายุ")

        # ตรวจสอบว่ามี URL ของโปรโมชั่นและรูปภาพที่ถูกต้องหรือไม่
        if not promotion_url:
            promotion_url = "https://www.starbucks.co.th/promotions"  # URL เริ่มต้นหากไม่พบ URL
        if not image_url:
            image_url = "https://via.placeholder.com/1024"  # รูปภาพเริ่มต้นหากไม่พบ URL รูปภาพ

        body_contents = []
        
        # แสดง title
        body_contents.append({
            "type": "text",
            "text": title,
            "weight": "bold",
            "size": "xl",
            "wrap": True
        })

        # แสดง details
        body_contents.append({
            "type": "text",
            "text": details[:100] + '...' if len(details) > 100 else details,
            "wrap": True,
            "margin": "md",
            "size": "sm"
        })

        # แสดงวันหมดอายุ
        body_contents.append({
            "type": "text",
            "text": f"หมดเขต: {expiry_date}",
            "wrap": True,
            "margin": "md",
            "size": "sm",
            "color": "#FF0000"  # ใช้สีแดงสำหรับแสดงวันหมดอายุ
        })

        # สร้าง bubble สำหรับโปรโมชั่น
        bubble = {
            "type": "bubble",
            "hero": {
                "type": "image",
                "url": image_url,  # ใช้รูปภาพจากโปรโมชั่นหรือรูปภาพเริ่มต้น
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover",
                "action": {
                    "type": "uri",
                    "uri": promotion_url  # ลิงก์ไปยัง URL ของโปรโมชั่น
                }
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": body_contents
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "uri",
                            "label": "ดูรายละเอียดเพิ่มเติม",
                            "uri": promotion_url  # ลิงก์ไปยัง URL ของโปรโมชั่น
                        }
                    }
                ]
            }
        }

        bubbles.append(bubble)

    # สร้าง Flex Message แบบ carousel
    if bubbles:
        flex_message = FlexSendMessage(
            alt_text='โปรโมชั่นล่าสุดจาก Starbucks',
            contents={
                "type": "carousel",
                "contents": bubbles  # ใส่ bubbles ที่สร้างไว้
            }
        )
        return flex_message
    else:
        return None


# ฟังก์ชันเพื่อดึงข้อมูลโปรโมชั่นเพิ่มเติม
def get_more_promotions(seen_titles):
    with driver.session() as session:
        result = session.run("MATCH (n:Promotion) RETURN n")
        promotions = []
        for record in result:
            node = record["n"]
            title = node.get("title")
            if title and title not in seen_titles:
                promotions.append({
                    "title": title,
                    "detail": node.get("details")[:100],  # จำกัดรายละเอียดไม่ให้ยาวเกิน 100 ตัวอักษร
                    "expiry_date": node.get("date"),
                    "promotion_url": node.get("promotion_url"),
                    "image_url": node.get("image")
                })
                seen_titles.add(title)
            if len(promotions) == 5:
                break
        return promotions

def get_products_by_category_name(category_name):
    try:
        with neo4j_driver.session() as session:
            # Query ค้นหาสินค้าที่อยู่ในหมวดหมู่ที่ระบุ พร้อมกับราคาในหลายรูปแบบ
            cypher_query = '''
            MATCH (c:Category {name: $category_name})-[:CONTAINS]->(p:Product)
            RETURN p.title as title, p.detail as detail, p.image_url as image_url, 
                   p.price as price, p.priceGrande as priceGrande, p.priceTall as priceTall, 
                   p.priceVenti as priceVenti, p.product_url as product_url
            '''
            results = session.run(cypher_query, category_name=category_name)
            
            # เก็บผลลัพธ์ทั้งหมดในรูปแบบ list
            products = []
            for record in results:
                products.append({
                    "title": record["title"],
                    "detail": record["detail"],
                    "image_url": record["image_url"],
                    "price": record["price"],  # ราคาดั้งเดิม
                    "priceGrande": record["priceGrande"],  # ราคา Grande
                    "priceTall": record["priceTall"],  # ราคา Tall
                    "priceVenti": record["priceVenti"],  # ราคา Venti
                    "product_url": record["product_url"]
                })
            
            # หากไม่มีสินค้าที่ตรงกับหมวดหมู่
            if not products:
                logging.warning(f"No products found for category: {category_name}")
                return None

            return products
    except Exception as e:
        logging.error(f"Error retrieving products from category: {str(e)}")
        return None

def format_similar_products(similar_products):
    unique_products = set()  # ใช้ set เพื่อตรวจสอบว่าได้แสดงสินค้านี้แล้วหรือไม่
    product_titles = []

    for product in similar_products:
        title = product['title']
        # ตรวจสอบว่าชื่อสินค้านี้ยังไม่ถูกแสดง
        if title not in unique_products:
            unique_products.add(title)  # บันทึกชื่อสินค้าใน set
            product_titles.append(f"• {title}")  # เพิ่มชื่อสินค้าใน list ที่จะแสดงผล

    # ถ้ามีสินค้าที่จะแสดง
    if product_titles:
        return "ไม่พบข้อมูลที่ตรงเป๊ะ แต่ลองดูสินค้าที่คล้ายกัน:\n" + "\n".join(product_titles)
    else:
        return "ไม่พบสินค้าที่คล้ายกัน"


# ฟังก์ชันที่ใช้ในการค้นหาข้อความที่คล้ายที่สุด และแสดงรายการสินค้าที่คล้ายกัน
def get_similar_products(user_message, threshold=0.5, top_k=3):
    try:
        # คำนวณเวกเตอร์จากข้อความผู้ใช้
        user_vector = model.encode([user_message], convert_to_tensor=True, normalize_embeddings=True).cpu().numpy()

        # ค้นหาข้อความใน corpus ที่คล้ายกับข้อความผู้ใช้ที่สุด
        D, I = index.search(user_vector.astype('float32'), k=top_k)

        # รวบรวมสินค้าที่มีความคล้ายกันเกิน threshold
        similar_products = []
        for i in range(len(D[0])):
            similarity = 1 - D[0][i]  # คำนวณ similarity จากระยะทาง L2
            if similarity >= threshold:
                product_title = corpus[I[0][i]]
                similar_products.append({
                    "title": product_title,
                    "similarity": similarity,
                    "details": responses[product_title]
                })

        return similar_products if similar_products else None
    except Exception as e:
        logging.error(f"Error finding similar products: {str(e)}")
        return None
    

def save_chat_to_neo4j(user_id, message, message_type):
    with driver.session() as session:
        result = session.run(
            """
            // ค้นหาหรือสร้างโหนด User ที่มี userId ตรงกัน
            MERGE (u:User {userId: $user_id})  
            
            // สร้างโหนด Chat ใหม่สำหรับข้อความที่รับมา
            CREATE (c:Chat { 
                timestamp: datetime(),         // บันทึกเวลาปัจจุบัน
                content: $content,             // บันทึกเนื้อหาข้อความ
                type: $type                    // บันทึกประเภทข้อความ (เช่น message, image)
            })
            
            // สร้างความสัมพันธ์ SENT ระหว่างผู้ใช้และข้อความที่ส่ง
            CREATE (u)-[:SENT]->(c)
            
            // ส่งค่าไปยังคำสั่งต่อไป
            WITH u, c
            
            // ค้นหาข้อความล่าสุดที่ผู้ใช้นี้เคยส่ง โดยไม่รวมข้อความใหม่ที่เพิ่งสร้าง
            OPTIONAL MATCH (u)-[:SENT]->(lastChat:Chat)
            WHERE lastChat <> c
            
            // จัดเรียงตามเวลาที่ส่งข้อความล่าสุด
            WITH u, c, lastChat
            ORDER BY lastChat.timestamp DESC
            LIMIT 1
            
            // หากพบข้อความล่าสุด (lastChat) สร้างความสัมพันธ์ NEXT ระหว่างข้อความเก่าและข้อความใหม่
            FOREACH (_ IN CASE WHEN lastChat IS NOT NULL THEN [1] ELSE [] END |
                CREATE (lastChat)-[:NEXT]->(c)
            )
            
            RETURN c   // คืนค่าโหนด Chat ที่สร้างใหม่
            """,
            user_id=user_id,
            content=message,
            type=message_type
        )
        return result.single() is not None
    
def sort_products_by_price(products, order="asc"):
    return sorted(products, key=lambda x: x.get('price', 0), reverse=(order == "desc"))

greeting_variants = [
    "สวัสดีครับ! ยินดีต้อนรับสู่ Starbucks",
    "หวัดดีครับ! วันนี้สนใจรับอะไรดีครับ",
    "สวัสดีครับ ช่วยแนะนำอะไรได้ไหมครับ"
]
greeting = random.choice(greeting_variants)


    

def delete_chat_history(user_id):
    with driver.session() as session:
        session.run(
            """
            MATCH (u:User {userId: $user_id})-[:SENT]->(c:Chat)
            DETACH DELETE c
            """, 
            user_id=user_id
        )
def save_displayed_products(user_id, category, displayed_products):
    with driver.session() as session:
        session.run(
            """
            MERGE (u:User {userId: $user_id})
            MERGE (u)-[:VIEWED]->(cat:Category {name: $category})
            SET cat.displayedProducts = $displayed_products
            """, 
            user_id=user_id, 
            category=category,
            displayed_products=displayed_products
        )


seen_titles = set()

def get_starbucks_recommendation(user_message):
    instruction = (
        f"คุณเป็น AI ผู้เชี่ยวชาญเกี่ยวกับ Starbucks ในประเทศไทย "
        f"กรุณาให้คำแนะนำเกี่ยวกับเมนูที่เหมาะสมสำหรับลูกค้าที่พูดว่า: '{user_message}'\n"
        f"ตอบเป็นภาษาไทย กระชับ ตรงประเด็น ไม่เกิน 2 ประโยค"
    )

    payload = {
        "model": "supachai/llama-3-typhoon-v1.5",
        "prompt": instruction,
        "max_tokens": 50,
        "stream": False
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(OLLAMA_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "ขออภัย ไม่สามารถให้คำแนะนำได้ในขณะนี้")
    except requests.exceptions.RequestException as e:
        logging.error(f"Ollama API error: {str(e)}")
        return "ขออภัย เกิดข้อผิดพลาดในการให้คำแนะนำ"



# Function to handle incoming messages
app = Flask(__name__)
@app.route("/", methods=['POST'])
def linebot():
    try:
        body = request.get_json()
        events = body.get('events', [])

        # Quick Reply setup for all responses
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="เมนูยอดนิยม", text="เมนูยอดนิยมของ Starbucks")),
                QuickReplyButton(action=MessageAction(label="โปรโมชั่นล่าสุด", text="โปรโมชั่นล่าสุดของ Starbucks")),
                QuickReplyButton(action=MessageAction(label="แนะนำเครื่องดื่ม", text="แนะนำเครื่องดื่มจาก Starbucks")),
                QuickReplyButton(action=MessageAction(label="ข้อมูลโปรเพิ่ม", text="โปรโมชั่นเพิ่มเติม")),
            ]
        )


        # Quick Reply for Drinks
        quick_reply_drinks = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="กาแฟร้อน", text="กาแฟร้อน")),
                QuickReplyButton(action=MessageAction(label="ชาร้อน", text="ชาร้อน")),
                QuickReplyButton(action=MessageAction(label="เครื่องดื่มร้อนอื่นๆ", text="เครื่องดื่มร้อนอื่นๆ")),
                QuickReplyButton(action=MessageAction(label="กาแฟเย็น", text="กาแฟเย็น")),
                QuickReplyButton(action=MessageAction(label="ชาเย็น", text="ชาเย็น")),
                QuickReplyButton(action=MessageAction(label="เครื่องดื่มเย็นอื่นๆ", text="เครื่องดื่มเย็นอื่นๆ")),
                QuickReplyButton(action=MessageAction(label="แฟรบปูชิโน่", text="เครื่องดื่มแฟรบปูชิโน่")),
                QuickReplyButton(action=MessageAction(label="กาแฟรีเสิร์ฟ", text="เครื่องดื่มจากกาแฟรีเสิร์ฟ")),
                QuickReplyButton(action=MessageAction(label="รีเสิร์ฟ เจ้าพระยา", text="รีเสิร์ฟ เจ้าพระยา ริเวอร์ฟร้อนท์")),
                QuickReplyButton(action=MessageAction(label="เครื่องดื่มบรรจุขวด", text="เครื่องดื่มบรรจุขวดพร้อมดื่ม")),
            ]
        )

        # Quick Reply for Food
        quick_reply_food = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="เมนูอาหารใหม่", text="เมนูอาหารใหม่")),
                QuickReplyButton(action=MessageAction(label="เบเกอรี่", text="เบเกอรี่")),
                QuickReplyButton(action=MessageAction(label="แซนด์วิช", text="แซนด์วิชและบิสโทร")),
                QuickReplyButton(action=MessageAction(label="ซุปและพาสต้า", text="ซุปและพาสต้า")),
                QuickReplyButton(action=MessageAction(label="สลัดและโยเกิร์ต", text="สลัดและโยเกิร์ต")),
            ]
        )

        # Quick Reply for Desserts
        quick_reply_desserts = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="ขนมหวานและไอศกรีม", text="ขนมของหวานและไอศกรีม")),
                QuickReplyButton(action=MessageAction(label="ขนมอบสดใหม่", text="ขนมอบ สด ใหม่ที่ร้าน")),
            ]
        )

        for event in events:
            if event['type'] == 'message':
                user_message = event['message']['text']
                user_id = event['source']['userId']
                
                # Save chat to Neo4j (Assuming function exists)
                save_chat_to_neo4j(user_id, user_message, "message")

                # Handle "ลบ" message (clear chat history)
                if user_message == "ลบ":
                    delete_chat_history(user_id)
                    response_msg = TextSendMessage(text="ลบประวัติการแชทเรียบร้อย", quick_reply=quick_reply)
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle request for Starbucks menu categories
                if user_message == "เมนูยอดนิยมของ Starbucks":
                    response_msg = TextSendMessage(
                        text="เลือกหมวดหมู่ที่คุณสนใจ",
                        quick_reply=QuickReply(
                            items=[
                                QuickReplyButton(action=MessageAction(label="เครื่องดื่ม", text="เครื่องดื่ม")),
                                QuickReplyButton(action=MessageAction(label="อาหาร", text="อาหาร")),
                                QuickReplyButton(action=MessageAction(label="ของหวาน", text="ขนมและของหวาน")),
                            ]
                        )
                    )
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                    # Handle request for Starbucks menu categories
                if user_message == "แนะนำเครื่องดื่มจาก Starbucks":
                    response_msg = TextSendMessage(
                        text='เลือกหมวดหมู่เครื่องดื่มที่คุณสนใจ',
                        quick_reply=quick_reply_drinks
                    )
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle specific category selection with Quick Reply for Drinks
                if user_message == "เครื่องดื่ม":
                    response_msg = TextSendMessage(
                        text="เลือกหมวดหมู่เครื่องดื่มที่คุณสนใจ",
                        quick_reply=quick_reply_drinks
                    )
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle specific category selection with Quick Reply for Food
                if user_message == "อาหาร":
                    response_msg = TextSendMessage(
                        text="เลือกหมวดหมู่อาหารที่คุณสนใจ",
                        quick_reply=quick_reply_food
                    )
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle specific category selection with Quick Reply for Desserts
                if user_message == "ขนมและของหวาน":
                    response_msg = TextSendMessage(
                        text="เลือกหมวดหมู่ของหวานที่คุณสนใจ",
                        quick_reply=quick_reply_desserts
                    )
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle category selection from Quick Reply
                category_list = [
                    "กาแฟร้อน", "ชาร้อน", "เครื่องดื่มร้อนอื่นๆ", "กาแฟเย็น", "ชาเย็น", "เครื่องดื่มเย็นอื่นๆ", 
                    "เครื่องดื่มแฟรบปูชิโน่", "เครื่องดื่มจากกาแฟรีเสิร์ฟ", "รีเสิร์ฟ เจ้าพระยา ริเวอร์ฟร้อนท์",
                    "เครื่องดื่มบรรจุขวดพร้อมดื่ม", "เมนูอาหารใหม่", "เบเกอรี่", "แซนด์วิชและบิสโทร",
                    "ซุปและพาสต้า", "สลัดและโยเกิร์ต", "ขนมของหวานและไอศกรีม", "ขนมอบ สด ใหม่ที่ร้าน"
                ]

                if user_message in category_list:
                    products = get_products_by_category_name(user_message)

                    if products:
                        flex_message = create_flex_message_for_products(products)
                        quick_reply_after_product = TextSendMessage(
                            text="ต้องการดูสินค้าหมวดอื่นหรือไหม?",
                            quick_reply=QuickReply(
                                items=[
                                    QuickReplyButton(action=MessageAction(label="เมนูอาหารใหม่", text="เมนูอาหารใหม่")),
                                    QuickReplyButton(action=MessageAction(label="เบเกอรี่", text="เบเกอรี่")),
                                    QuickReplyButton(action=MessageAction(label="แซนด์วิช", text="แซนด์วิชและบิสโทร")),
                                    QuickReplyButton(action=MessageAction(label="ซุปและพาสต้า", text="ซุปและพาสต้า")),
                                    QuickReplyButton(action=MessageAction(label="สลัดและโยเกิร์ต", text="สลัดและโยเกิร์ต")),
                                    QuickReplyButton(action=MessageAction(label="โปรโมชั่นล่าสุด", text="โปรโมชั่นล่าสุดของ Starbucks")),

                                ]
                            )
                        )
                        line_bot_api.reply_message(event['replyToken'], [flex_message, quick_reply_after_product])
                    else:
                        response_msg = TextSendMessage(text="ไม่พบสินค้าที่คุณเลือก", quick_reply=quick_reply)
                        line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle viewing detailed information for a product
                if "ข้อมูลเพิ่มเติม" in user_message:
                    # Extract category or product name from the latest message, removing "ข้อมูลเพิ่มเติม "
                    category_or_product = user_message.replace("ข้อมูลเพิ่มเติม ", "")
                    
                    # Check if the extracted value matches a category or a product title
                    products = get_products_by_category_name(category_or_product)  # Try fetching by category first

                    if not products:
                        # If no products found by category, search by product title
                        product = get_product_from_neo4j(category_or_product)

                        if product:
                            # Only show the product if it hasn't been shown before
                            if product['title'] not in seen_titles:
                                detail_message = FlexSendMessage(
                                    alt_text=f"รายละเอียดของ {product['title']}",
                                    contents={
                                        "type": "bubble",
                                        "body": {
                                            "type": "box",
                                            "layout": "vertical",
                                            "contents": [
                                                {"type": "text", "text": product['title'], "weight": "bold", "size": "lg"},
                                                {"type": "text", "text": f"ราคา: {product['price']}"},
                                                {"type": "text", "text": f"รายละเอียด: {product['detail']}"}
                                            ]
                                        }
                                    }
                                )
                                line_bot_api.reply_message(event['replyToken'], detail_message)
                                seen_titles.add(product['title'])  # Add the product to the seen set
                            else:
                                response_msg = TextSendMessage(text="สินค้านี้เคยแสดงไปแล้ว", quick_reply=quick_reply)
                                line_bot_api.reply_message(event['replyToken'], response_msg)
                        else:
                            # If no product or category is found, respond with a message
                            response_msg = TextSendMessage(text="ไม่พบข้อมูลสินค้านี้", quick_reply=quick_reply)
                            line_bot_api.reply_message(event['replyToken'], response_msg)
                    else:
                        # Check if there are more than 12 products
                        if len(products) > 12:
                            # Filter out the products that have already been shown
                            products_to_show = [p for p in products if p['title'] not in seen_titles]

                            if products_to_show:
                                for product in products_to_show[:12]:  # Show up to 12 products
                                    detail_message = FlexSendMessage(
                                        alt_text=f"รายละเอียดของ {product['title']}",
                                        contents={
                                            "type": "bubble",
                                            "body": {
                                                "type": "box",
                                                "layout": "vertical",
                                                "contents": [
                                                    {"type": "text", "text": product['title'], "weight": "bold", "size": "lg"},
                                                    {"type": "text", "text": f"ราคา: {product['price']}"},
                                                    {"type": "text", "text": f"รายละเอียด: {product['detail']}"}
                                                ]
                                            }
                                        }
                                    )
                                    line_bot_api.reply_message(event['replyToken'], detail_message)
                                    seen_titles.add(product['title'])  # Add the product to the seen set
                            else:
                                response_msg = TextSendMessage(text="ไม่มีสินค้าที่เหลือที่จะแสดง", quick_reply=quick_reply)
                                line_bot_api.reply_message(event['replyToken'], response_msg)
                        else:
                            response_msg = TextSendMessage(text="มีสินค้าที่น้อยกว่า 12 รายการ จึงไม่แสดงเพิ่มเติม", quick_reply=quick_reply)
                            line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle promotion request
                elif user_message == "โปรโมชั่นล่าสุดของ Starbucks":
                    promotion = get_promotion_data()
                    if promotion:
                        messages = create_flex_message_for_promotions(promotion)
                        line_bot_api.reply_message(event['replyToken'], messages)
                    else:
                        response_msg = TextSendMessage(text="ไม่พบข้อมูลโปรโมชัน", quick_reply=quick_reply)
                        line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle greeting messages
                elif is_greeting(user_message):
                    greeting = get_greeting_from_neo4j()
                    response_msg = TextSendMessage(text=greeting, quick_reply=quick_reply)
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle general product queries
            elif is_greeting(user_message):
                    greeting = get_greeting_from_neo4j()
                    response_msg = TextSendMessage(text=greeting, quick_reply=quick_reply)
                    line_bot_api.reply_message(event['replyToken'], response_msg)
                    continue

                # Handle general product queries
            else:
                product = get_product_from_neo4j(user_message)

                if product:
                    # ใช้ Ollama เพื่อปรับปรุงข้อความจาก Neo4j
                    improved_description = ollama_new(f"ปรับปรุงคำอธิบายสินค้านี้ให้น่าสนใจ: {product['detail']}")
                    
                    # สร้าง Flex Message ด้วยข้อมูลที่ปรับปรุงแล้ว
                    flex_content = {
                        "type": "bubble",
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": product['title'], "weight": "bold", "size": "xl"},
                                {"type": "text", "text": f"ราคา: {product['price']}"},
                                {"type": "text", "text": improved_description, "wrap": True}
                            ]
                        }
                    }
                    
                    response_msg = FlexSendMessage(alt_text=f"รายละเอียดของ {product['title']}", contents=flex_content)
                    quick_reply_after_flex = TextSendMessage(
                        text="ต้องการดูหมวดหมู่อื่นหรือโปรโมชั่นเพิ่มเติมไหม?",
                        quick_reply=quick_reply)
                    line_bot_api.reply_message(event['replyToken'], [response_msg, quick_reply_after_flex])
                else:
                    # ไม่พบสินค้า ใช้ Ollama เพื่อแนะนำ
                    ollama_response = ollama_new(f"แนะนำเครื่องดื่มหรืออาหารที่เกี่ยวข้องกับ: {user_message}")
                    response_msg = TextSendMessage(text=ollama_response, quick_reply=quick_reply)
                    line_bot_api.reply_message(event['replyToken'], response_msg)
        return 'OK'
    except Exception as e:
        logging.error(f"Error handling request: {str(e)}")
        return jsonify({"error": "An error occurred"}), 500

if __name__ == "__main__":
    app.run(port=5201)
