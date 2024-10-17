from flask import Flask, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json
import logging
import re
import os
import requests
from neo4j import GraphDatabase

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

class Neo4jConnection:
    def __init__(self, uri, username, password):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self):
        self.driver.close()

    def run_query(self, query, parameters=None):
        with self.driver.session() as session:
            if parameters:
                result = session.run(query, parameters)
            else:
                result = session.run(query)
            return [record for record in result]

def setup_driver():
    service = Service(ChromeDriverManager().install())
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    return webdriver.Chrome(service=service, options=chrome_options)

def load_page(driver, url):
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'col-inner'))
        )
    except Exception as e:
        logging.error(f"Error loading page: {e}")
        raise

def extract_prices(product):
    prices = {}
    price_container = product.find('div', class_='wccpf-admin-fields-group-1')

    if price_container:
        price_elements = price_container.find_all('p', class_='wcff-wccaf-value-para-tag')
        for price_elem in price_elements:
            label = price_elem.find_previous('label').text.strip()
            price_value = price_elem.text.strip()
            prices[label] = price_value

    price_span = product.find('span', class_='woocommerce-Price-amount amount')
    if price_span:
        price_amount = price_span.find('bdi').text.strip()
        currency = price_span.find('span', class_='woocommerce-Price-currencySymbol').text.strip()
        prices['price'] = f"{price_amount}"

    return prices if prices else None

def parse_menu_page(driver):
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        sections = soup.find_all('div', class_='col-inner')
        menu_data = {}

        for section in sections:
            header = section.find('h2')
            if header:
                category = header.text.strip()
                products = section.find_all('div', class_=re.compile(r'product-small|product|type-product|status-publish|instock'))

                products_data = []

                for product in products:
                    title_elem = product.find('p', class_='name product-title woocommerce-loop-product__title')
                    detail_elem = product.find('p', class_='box-excerpt')
                    image_elem = product.select_one('div.image-zoom > a > img')

                    if image_elem:
                        image_url = image_elem.get('data-src', image_elem.get('src', ''))
                        if 'data:image/svg+xml' in image_url or not image_url:
                            logging.warning(f"Found SVG image or empty URL for {title_elem.text.strip()}")
                            continue

                    product_link_elem = product.find('a', class_='woocommerce-loop-product__link')
                    product_url = product_link_elem['href'] if product_link_elem else 'ไม่พบ URL'

                    if title_elem and detail_elem and image_url:
                        title = title_elem.text.strip()
                        detail = detail_elem.text.strip()

                        prices = extract_prices(product)

                        if prices:
                            products_data.append({
                                'title': title,
                                'detail': detail,
                                'prices': prices,
                                'image_url': image_url,
                                'product_url': product_url
                            })

                menu_data[category] = products_data

        return menu_data
    except Exception as e:
        logging.error(f"Error parsing menu page: {e}")
        raise

def scrape_starbucks_promotions():
    url = 'https://www.starbucks.co.th/th/promotions/'
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    promotions = []

    for promo in soup.find_all('div', class_='col post-item'):
        title = promo.find('h5', class_='post-title is-large').get_text(strip=True)
        link = promo.find('a', class_='plain')['href']
        
        # Extract image URL
        image_elem = promo.find('img', class_='attachment-medium')
        if image_elem:
            image_url = image_elem.get('data-src', image_elem.get('src', ''))
            
            # Ensure the URL is absolute
            if image_url.startswith('//'):
                image_url = 'https:' + image_url
        else:
            image_url = 'Image not found'

        date = promo.find('p', class_='from_the_blog_excerpt').get_text(strip=True)

        # ดึงรายละเอียดของโปรโมชั่นจากลิงก์ของโปรโมชั่น
        details = scrape_promotion_details(link).get('details', 'No details found')

        promotions.append({
            'title': title,
            'link': link,
            'image': image_url,
            'date': date,
            'details': details  # กำหนดค่า details ที่ดึงมาจากลิงก์
        })
    
    return promotions

def scrape_promotion_details(link):
    """Scrape promotion details from a specific link."""
    try:
        response = requests.get(link)
        response.raise_for_status()  # Check for HTTP errors
    except requests.RequestException as e:
        print(f"Error fetching promotion details: {e}")
        return {}
    
    soup = BeautifulSoup(response.content, 'html.parser')

    # ดึงข้อมูลจาก <div class="entry-content single-page">
    content_div = soup.find('div', class_='entry-content single-page')
    
    if content_div:
        # ดึงข้อมูลจากแต่ละ <p> ที่อยู่ภายใน
        paragraphs = content_div.find_all('p')
        
        details = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if text:  # เก็บเฉพาะข้อความที่ไม่ว่างเปล่า
                details.append(text)
        
        # แสดงผลข้อมูลที่ดึงมา
        return {
            'details': details
        }
    else:
        return {
            'details': 'No content found'
        }

def upload_data_to_neo4j(menu_data, promotions_data):
    neo4j_url = "bolt://localhost:7687"
    neo4j_username = "neo4j"
    neo4j_password = "Lutfee2salaeh"

    neo4j_conn = Neo4jConnection(neo4j_url, neo4j_username, neo4j_password)

    try:
        # Clear duplicates in Category, Product, and Promotion
        queries = [
            """
            MATCH (c:Category)
            WITH c.name AS categoryName, collect(c) AS categories
            WHERE size(categories) > 1
            UNWIND categories[1..] AS toDelete
            // Remove relationships before deleting the node
            MATCH (toDelete)-[r]-()
            DELETE r
            DELETE toDelete
            """,
            """
            MATCH (p:Product)
            WITH p.title AS productTitle, collect(p) AS products
            WHERE size(products) > 1
            UNWIND products[1..] AS toDelete
            // Remove relationships before deleting the node
            MATCH (toDelete)-[r]-()
            DELETE r
            DELETE toDelete
            """,
            """
            MATCH (promo:Promotion)
            WITH promo.title AS promoTitle, collect(promo) AS promotions
            WHERE size(promotions) > 1
            UNWIND promotions[1..] AS toDelete
            // Remove relationships before deleting the node
            MATCH (toDelete)-[r]-()
            DELETE r
            DELETE toDelete
            """
        ]
        
        for query in queries:
            neo4j_conn.run_query(query)

        print("ลบไฟล์ซ้ำเสร็จแล้ว")

        # Upload menu data
        for category, products in menu_data.items():
            if products:
                query = """
                MERGE (c:Category {name: $category})
                """
                neo4j_conn.run_query(query, {"category": category})

                for product in products:
                    if 'prices' in product and product['prices'].get('price'):
                        query = """
                        CREATE (p:Product {title: $title, detail: $detail, price: $price, image_url: $image_url, product_url: $product_url})
                        MERGE (c:Category {name: $category})
                        MERGE (c)-[:CONTAINS]->(p)
                        """
                        neo4j_conn.run_query(query, {
                            "title": product['title'],
                            "detail": product['detail'],
                            "price": product['prices']['price'],
                            "image_url": product['image_url'],
                            "product_url": product['product_url'],
                            "category": category
                        })

        print("กำลังอัปโหลดข้อมูลเมนู...")

        logging.info("เมนูทั้งหมดโหลดเสร็จแล้ว")

        # Upload promotions data
        for promotion in promotions_data:
            if promotion.get('details'):
                query = """
                CREATE (promo:Promotion {title: $title, link: $link, image: $image, date: $date, details: $details})
                """
                neo4j_conn.run_query(query, {
                    "title": promotion['title'],
                    "link": promotion['link'],
                    "image": promotion['image'],
                    "date": promotion['date'],
                    "details": ' '.join(promotion['details'])
                })

        print("กำลังอัปโหลดข้อมูลโปรโมชั่น...")

        logging.info("โปรโมชั่นทั้งหมดโหลดเสร็จแล้ว")

    except Exception as e:
        logging.error(f"Error uploading data to Neo4j: {e}")
    finally:
        neo4j_conn.close()



@app.route('/')
def index():
    url = 'https://www.starbucks.co.th/th/menu/'
    driver = None
    try:
        driver = setup_driver()
        load_page(driver, url)
        menu_data = parse_menu_page(driver)

        # แยกเก็บไฟล์ JSON สำหรับแต่ละหมวดหมู่
        if not os.path.exists('menu_json'):
            os.makedirs('menu_json')

        for category, products in menu_data.items():
            category_file = os.path.join('menu_json', f"{category}.json")
            with open(category_file, 'w', encoding='utf-8') as f:
                json.dump(products, f, indent=4, ensure_ascii=False)

        # ดึงข้อมูลโปรโมชั่น
        promotions_data = scrape_starbucks_promotions()

        # บันทึกข้อมูลโปรโมชั่นเป็นไฟล์ JSON
        promotions_file = os.path.join('menu_json', 'starbucks_promotions.json')
        with open(promotions_file, 'w', encoding='utf-8') as f:
            json.dump(promotions_data, f, indent=4, ensure_ascii=False)

        # อัปโหลดข้อมูลไปยัง Neo4j
        upload_data_to_neo4j(menu_data, promotions_data)
           # รวมข้อมูลเมนูและโปรโมชั่น
        combined_data = {
            'menu': menu_data,
            'promotions': promotions_data
        }

        return jsonify(combined_data)

        return jsonify({"message": "Data scraped and uploaded successfully!"})

    except Exception as e:
        logging.error(f"Error in main application: {e}")
        return jsonify({"error": str(e)}), 500

    finally:
        if driver:
            driver.quit()

if __name__ == '__main__':
    app.run(debug=True)
