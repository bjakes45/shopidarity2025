from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Product, Rating, Favorite, Comment, ProductStatus, Deal, APIUsage
from functools import wraps
from datetime import datetime, timedelta
import geopy, json, os, random, csv, requests
import re
import pandas as pd
from datetime import datetime
from sqlalchemy import func, or_
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
from faker import Faker
from flask_wtf import CSRFProtect
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests.exceptions
from google.cloud import translate_v2 as translate
from langdetect import detect




from helpers import (
    login_required_with_redirect_back,
    get_user_favorites,
    get_user_ratings,
    find_similar_users,
    recommend_products,
    get_potential_groups,
    get_working_image_url,
    seed_products,
    seed_plus,
    seed_users_with_interactions,
    seed_deals,
    get_client_ip,
    enforce_rate_limit,
    get_paginated,
    faker
)

load_dotenv()


#APP CONTEXT
API_USAGE = {
    "count": 0,
    "reset_time": datetime.utcnow() + timedelta(days=1),
    "remaining": 100,  # starting assumption
    "reset_timestamp": None  # from UPCDB header
}

#Init server and database connection
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
csrf = CSRFProtect(app)
if os.environ.get('RENDER'):
    # Running on Render – require DATABASE_URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in environment on Render.")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Local development fallback
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
translate_client = translate.Client()


# Initialize Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # name of your login route

def not_in_english(text):
    try:
        lang = detect(text)
        return lang != 'en', lang
    except:
        return False, 'unknown'

@app.before_request
def initialize_database():
    db.create_all()

    if Product.query.count() == 0:
        seed_products()    

    # Ensure PLU seed only happens once
    plu_count = Product.query.filter(db.func.length(Product.upc) == 4).count()
    if plu_count == 0:
        seed_plus()
    
    if User.query.filter_by(email=os.getenv("ADMIN_EMAIL")).first() is None:
      user = User(
        username=os.getenv("ADMIN_USERNAME"),
        email=os.getenv("ADMIN_EMAIL"),
        password=generate_password_hash(os.getenv("ADMIN_PASSWORD")),
        city=os.getenv("ADMIN_CITY"),
        country=os.getenv("ADMIN_COUNTRY"),
        admin=True,
        latitude=float(os.getenv("ADMIN_LAT")),
        longitude=float(os.getenv("ADMIN_LON"))
      )
      db.session.add(user)
      db.session.commit()

    #if User.query.count() <= 1:
    #  seed_users_with_interactions()

    if Deal.query.count() <= 100:
      seed_deals()


# User loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

#HOMEPAGE
@app.route('/')
def home():
	return render_template('home.html', current_year=datetime.now().year)

#UPC SCANNER
@app.route('/scan')
def scan():
	return render_template('scan.html')

#PRODUCTS
@app.route('/products', methods=['GET'])
def products():
    #products_all = Product.query.all()
    # Query all products where UPC is exactly 4 characters long
    products_all = Product.query.all()
    db_total = len(products_all)

    query = request.args.get('query', '')  # Retrieve UPC query if available

    page = request.args.get('page', 1, type=int)
    per_page = 12

    products = Product.query.filter_by(status=ProductStatus.APPROVED).all()
    
    if query:
        products = Product.query.filter(
        or_(
            Product.upc.ilike(f"%{query}%"),
            Product.name.ilike(f"%{query}%"),
            Product.description.ilike(f"%{query}%"),
            Product.category.ilike(f"%{query}%")
        )
        ).filter_by(status=ProductStatus.APPROVED).all()
    
    pagination, page, total_pages, total = get_paginated(products, page, per_page=18)
    def url_builder(p):
        return url_for('products', page=p)
    
    return render_template('products/products.html', 
        total_display=total, 
        db_total=db_total,
        page=page,
        total_pages=total_pages, 
        pagination=pagination, 
        query=query, 
        url_builder=url_builder
    )

#PRODUCT DETAIL
@app.route('/products/<string:upc>')
def product_detail(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()
    average_rating = db.session.query(func.avg(Rating.score)).filter_by(product_upc=upc).scalar()
    comments = Comment.query.filter_by(product_upc=upc, is_public=True).order_by(Comment.timestamp.desc()).limit(3).all()
    average_price = None	
    total_price = 0
    
    for deal in product.deals:
        total_price += deal.price
        if len(product.deals) > 0:
        	average_price = total_price / len(product.deals)  

	# If logged in, pull user-specific data
    favorite = rating = private_comments = None
    if current_user.is_authenticated:
	    favorite = Favorite.query.filter_by(user_id=current_user.id, product_upc=upc).first()
	    rating = Rating.query.filter_by(user_id=current_user.id, product_upc=upc).first()
	    private_comments = Comment.query.filter_by(user_id=current_user.id, product_upc=upc, is_public=False).all()
    

    return render_template('products/product_detail.html', product=product, average_rating=average_rating, comments=comments, favorite=favorite, rating=rating, private_comments=private_comments, average_price=average_price)

#PRODUCT Nutrifacts
@app.route('/products/<string:upc>/nutrifacts')
def product_nutrifacts(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()

    if product.nutriments == None: 
    	redirect(url_for('product_detail', upc=product.upc))
    

    return render_template('products/product_nutrifacts.html', product=product)

#PRODUCT Deals
@app.route('/products/<string:upc>/deals')
def product_deals(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()

    if product.deals == None: 
    	redirect(url_for('product_detail', upc=product.upc))
    

    return render_template('products/product_deals.html', product=product)

#PRODUCT NEW DEAL

@app.route("/product/<string:upc>/new_deal", methods=["GET", "POST"])
def product_new_deal(upc):
    product = Product.query.get_or_404(upc)

    if request.method == "POST":
        store = request.form.get("store")
        if not store:
          store = request.form.get("store-input")
          flash(store)
        price_str = request.form.get("price")
        price = float(re.sub(r'[^\d.]', '', price_str)) if price_str else None
        url = request.form.get("url")
        user_id = current_user.id if current_user.is_authenticated else None
        lat = request.form.get("location-lat")
        lng = request.form.get("location-lng")
        if not lat or not lng:
          lat, lng = current_user.latitude, current_user.longitude

        if store and price:
            try:
                new_deal = Deal(
                    product_id=product.upc,
                    store=store,
                    price=float(price),
                    url=url or None,
                    latitude=lat,
                    longitude=lng,
                    user_id=user_id,
                    source="user",
                )
                db.session.add(new_deal)
                db.session.commit()
                flash("Thanks! Your deal was added.", "success")
                return redirect(url_for("product_detail", upc=product.upc))
            except Exception as e:
                flash(f"Error submitting deal: {e}", "danger")

    deals = product.deals
    return render_template("products/product_new_deal.html",
      upc=upc, 
      product=product,
      GOOGLE_API_KEY=os.environ.get('GOOGLE_API_KEY'),
      user_lat=current_user.latitude if current_user.is_authenticated else None,
      user_lng=current_user.longitude if current_user.is_authenticated else None
    )

#PRODUCT COMMENTS
@app.route('/products/<string:upc>/comments')
def product_comments(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()
    comments = Comment.query.filter_by(product_upc=upc, is_public=True).order_by(Comment.timestamp.desc()).limit(3).all()

	# If logged in, pull user-specific data
    private_comments = None
    if current_user.is_authenticated:
	    private_comments = Comment.query.filter_by(user_id=current_user.id, product_upc=upc, is_public=False).all()

    return render_template('products/product_comments.html', product=product, comments=comments, private_comments=private_comments)

#NEW PRODUCT
@app.route('/products/new', methods=['GET', 'POST'])
def new_product():
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)

    rate_limit_response = enforce_rate_limit()
    if rate_limit_response:
        return redirect(url_for('scan'))

    is_logged_in = current_user.is_authenticated

    upc = request.args.get('upc', '')  # Retrieve UPC query
    if not upc or len(upc) not in (12, 13):
        flash("Invalid UPC: must be 12 or 13 digits.", "error")
        return redirect(url_for('products'))
    
    existing_product = Product.query.filter_by(upc=upc).first()
    if existing_product:
    	return redirect(url_for('product_detail', upc=existing_product.upc))
    
    if request.method == 'POST':
        name = request.form['name']
        brand = request.form['brand']
        image_url = request.form['image_url']
        
        category = request.form['category']
        
        def trim_category_string(category_str, max_items=3):
            if not category_str:
                return ''
            parts = [p.strip() for p in category_str.split(',') if p.strip()]
            
            if len(parts) <= max_items:
                return ', '.join(parts)
            # Pick first (general) and last two (specific)
            trimmed = [parts[0]] + parts[-(max_items - 1):]
            return ', '.join(trimmed)
        
        if len(category) > 128:
            category = trim_category_string(category)
        if len(category) > 128:
            category = category[:125] + '...'
        
        nutriments_raw = request.form.get('nutriments')
        nutriments = None

        IMPORTANT_NUTRIENTS = [
            "energy-kcal", "fat", "saturated-fat", "carbohydrates", "sugars",
            "fiber", "proteins", "salt", "potassium", "calcium", "iron"
        ]

        def filter_important_nutrients(raw_nutriments):
            try:
                parsed = json.loads(raw_nutriments) if isinstance(raw_nutriments, str) else raw_nutriments
                return {k: v for k, v in parsed.items() if k in IMPORTANT_NUTRIENTS}
            except Exception as e:
               print(f"Failed to parse or filter nutriments: {e}")
            return None

        if nutriments_raw:
          try:
              nutriments_filter = filter_important_nutrients(nutriments_raw)
              nutriments = json.loads(nutriments_filter) if isinstance(nutriments_filter, str) else nutriments_filter
          except json.JSONDecodeError as e:
            flash(f"Invalid JSON in 'nutriments': {e}", "error")

        offers_raw = request.form.get('offers')
        try:
            offers = json.loads(offers_raw) if offers_raw else None
        except json.JSONDecodeError:
            offers = None  # fallback if invalid
        status = ProductStatus.APPROVED if is_logged_in else ProductStatus.SUGGESTED
        user_id = current_user.id if is_logged_in else None

        new_product = Product(
            upc=upc,
            name=name,
            category=category,
            brand=brand,
            image_url=image_url,
            nutriments=nutriments,
            status=status,
            suggested_by_ip=ip_address,
            user_id=user_id
        )
        db.session.add(new_product)
        db.session.flush()

        for offer in offers:
            try:
                deal = Deal(
                    product_id=new_product.upc,
                    price=offer.get("price"),
                    store=offer.get("merchant"),
                    url=offer.get("link"),
                    location=offer.get("region", "N/A"),
                    source="UPCitemDB"
                )
                db.session.add(deal)
            except Exception as e:
                print(f"Bad offer: {e}")
    
        db.session.commit()
    
        return redirect(url_for('product_detail', upc=upc))

    return render_template('products/new_product.html', upc=upc)

#DEAL DETAIL
@app.route("/deal/<int:deal_id>")
def deal_detail(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    return render_template("deals/deal_detail.html", deal=deal)

#USER DASHBOARD
@app.route('/dashboard/')
@login_required
def dashboard():
    user_id = current_user.id
    favorites = Favorite.query.filter_by(user_id=user_id).order_by(Favorite.timestamp.desc()).limit(3).all()
    ratings = Rating.query.filter_by(user_id=user_id).order_by(Rating.timestamp.desc()).limit(3).all()
    comments = Comment.query.filter_by(user_id=user_id).order_by(Comment.timestamp.desc()).limit(3).all()
    
    return render_template('dashboard/dashboard.html', favorites=favorites, ratings=ratings, comments=comments)

@app.route('/dashboard/added_products')
@login_required
def dashboard_added_products():
    added_products = Product.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/added_products.html', added_products=added_products)

@app.route('/dashboard/favorites')
@login_required
def dashboard_favorites():
    favorites = Favorite.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/favorites.html', favorites=favorites)

@app.route('/dashboard/ratings')
@login_required
def dashboard_ratings():
    ratings = Rating.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/ratings.html', ratings=ratings)

@app.route('/dashboard/comments')
@login_required
def dashboard_comments():
    comments = Comment.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/comments.html', comments=comments)

#SUGGESTIONS
@app.route("/dashboard/suggestions")
@login_required
def dashboard_suggestions():
    suggestions = Product.query.filter_by(status=ProductStatus.SUGGESTED).all()
    return render_template("dashboard/suggestions.html", products=suggestions, api_calls=API_USAGE["reset_time"])

@app.route("/suggestion/approve/<product_upc>", methods=["POST"])
@login_required
def approve_suggestion(product_upc):
    user_id = current_user.id
    product = Product.query.get_or_404(product_upc)
    product.status = ProductStatus.APPROVED
    product.user_id = user_id
    db.session.commit()
    flash(f"Product '{product.name}' approved.", "success")
    return redirect(url_for("dashboard_suggestions"))


@app.route("/member/reject/<product_upc>", methods=["POST"])
@login_required
def reject_suggestion(product_upc):
    product = Product.query.get_or_404(product_upc)
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product.name}' rejected and deleted.", "info")
    return redirect(url_for("dashboard_suggestions"))

#DISCOVERY
@app.route('/discover')
@login_required
def discover():
    tab = request.args.get('tab', 'users')  # default to 'users'
    
    user = current_user
    user_favorites = get_user_favorites(user.id)
    user_ratings = get_user_ratings(user.id)

    page = request.args.get('page', 1, type=int)
    total_pages = 1
    total_display = 0

    similar_users  = ''
    recommended_products = ''
    potential_groups = ''
    nearby_users = ''
    nearby_deals = ''

    if tab == 'users':
        similar_users = find_similar_users(current_user.id)
        similar_users, pages, total_pages, total_display = get_paginated(similar_users, page, per_page=9)
    if tab == 'products':
        similar_users = find_similar_users(current_user.id)
        recommended_products = recommend_products(user, other_users=similar_users)
        recommended_products, pages, total_pages, total_display = get_paginated(recommended_products, page, per_page=9)
    if tab == 'groups':
        potential_groups = get_potential_groups(user)
        potential_groups, pages, total_pages, total_display = get_paginated(potential_groups, page, per_page=9)
    if tab == 'deals':
        nearby_users = current_user.nearby_users(radius_km=40)
        nearby_deals = current_user.nearby_deals(radius_km=40)
        nearby_deals, pages, total_pages, total_display = get_paginated(nearby_deals, page, per_page=75)

    def url_builder(p):
        return url_for('discover', tab=tab, page=p)
    
    return render_template('discover.html',
                           similar_users=similar_users,
                           total_display=total_display,
                           tab=tab,
                           page=page, 
                           total_pages=total_pages,
                           recommended_products=recommended_products,
                           nearby_users=nearby_users,
                           potential_groups=potential_groups,
                           nearby_deals=nearby_deals,
                           url_builder=url_builder)


#CHECK UPC
@app.route('/api/check_upc/<upc>')
def check_upc(upc):
    product = Product.query.filter_by(upc=upc).first()

    if product:
        return jsonify({
            'exists': True,
            'product': {
                'name': product.name,
                'upc': product.upc,
                'category': product.category,
                'brand': product.brand
            }
        })
    else:
        return jsonify({'exists': False})

#API LOOKUP
@app.route('/api/lookup-upc/<upc>')
def lookup_upc(upc):
    # Try Open Food Facts first
    off_url = f"https://world.openfoodfacts.org/api/v0/product/{upc}.json"
    off_response = requests.get(off_url)

    if off_response.status_code == 200:
        off_data = off_response.json()
        if off_data.get('status') == 1:
            product = off_data['product']
            return jsonify({
                "source": "OFF",
                "data": {
                    "name": product.get('product_name', ''),
                    "brand": product.get('brands', ''),
                    "category": product.get('categories', ''),
                    "description": product.get('generic_name', ''),
                    "image_url": product.get("image_url"),
                    "nutriments": product.get("nutriments", {}),
                    "offers": {}
                }
            })

    # If OFF fails, fallback to UPCitemDB
    now = datetime.utcnow()
    if now > API_USAGE["reset_time"]:
        API_USAGE["count"] = 0
        API_USAGE["reset_time"] = now + timedelta(days=1)

    if API_USAGE["count"] >= 100:
        return jsonify({"error": "UPCitemDB daily limit reached."}), 429

    upcdb_response = requests.get(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={upc}",
        headers={"Content-Type": "application/json"}
    )

    if upcdb_response.ok:
    
        # Extract rate limit headers
        API_USAGE["count"] += 1
        API_USAGE["remaining"] = int(upcdb_response.headers.get("X-RateLimit-Remaining", 100))
        reset_unix = upcdb_response.headers.get("X-RateLimit-Reset")
        if reset_unix:
            API_USAGE["reset_timestamp"] = datetime.utcfromtimestamp(int(reset_unix))
            API_USAGE["reset_time"] = API_USAGE["reset_timestamp"]  # Keep consistent

        data = upcdb_response.json()
        items = data.get("items", [])
        if items:
            item = items[0]
            return jsonify({
                "source": "UPCDB",
                "data": {
                    "name": item.get('title', ''),
                    "brand": item.get('brand', ''),
                    "category": item.get('category', ''),
                    "description": item.get('description', ''),
                    "image_url": get_working_image_url(item.get("images", [])),
                    "nutriments": {},
                    "offers": item.get('offers', '')
                }
            })

    return jsonify({"error": "Product not found"}), 404
#CHECK LANGUAGE
@app.route('/api/check_language', methods=['POST'])
def api_check_language():
    text = request.json.get('text', '')
    if not text:
        return jsonify({'error': 'Missing text'}), 400

    result = not_in_english(text)
    return jsonify({'not_in_english': result})

#TRANSLATION
@app.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.get_json()
    text = data.get('text', '')

    translated = translate_client.translate(text, target_language='en')
    return jsonify({
        'original': text,
        'translated': translated['translatedText'],
        'detected_language': translated['detectedSourceLanguage']
    })

#SCRAPE DEAL
@app.route('/scrape_deal', methods=['POST'])
def scrape_deal():
    url = request.json.get('url')
    headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36',
      'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Try extracting some common data points
        title = soup.title.string.strip() if soup.title else ''
        price = ''  # You could try using soup.select to find prices
        store = 'Costco' if 'costco' in url else 'Unknown'

        return jsonify({
            'success': True,
            'product_name': title,
            'price': price,
            'store': store
        })
    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request timed out.'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Scraping failed: {str(e)}'})

#USER HANDLING
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('products'))
        else:
            flash('Invalid username or password')
    return render_template('user_admin/login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(request.referrer or url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_pw, city=request.form['city'])
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('products'))
    return render_template('user_admin/register.html')

@app.route('/dashboard/new_user', methods=['GET', 'POST'])
@login_required
def dashboard_new_user():
    if not current_user.admin:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        is_admin = 'admin' in request.form

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Username already exists.', 'error')
        else:
            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                admin=is_admin
            )
            db.session.add(user)
            db.session.commit()
            flash(f'User {username} created successfully.', 'success')
            return redirect(url_for('dashboard_new_user'))

    return render_template('dashboard/new_user.html')

# Whitelist of supported cities
#SUPPORTED_CITIES = {('Vancouver','Canada')}
SUPPORTED_CITIES = {}

@app.route('/check-location')
def check_location():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    try:
        geolocator = Nominatim(user_agent="shopidarity")
        location = geolocator.reverse(f"{lat}, {lon}", language='en')
        address = location.raw.get('address', {})
        city = (address.get('city') or address.get('town') or address.get('village') or '').strip().title()
        country = (address.get('country') or '').strip().title()

        allowed = (city, country) in SUPPORTED_CITIES

        return jsonify({
            "allowed": allowed,
            "city": city,
            "country": country,
            "user_count": User.query.filter_by(city=city, country=country).count()      # placeholder until we track cities on users
        })
    except Exception as e:
        return jsonify({
            "allowed": False,
            "city": "",
            "country": "",
            "user_count": User.query.filter_by(city=city, country=country).count(),
            "error": str(e)
        })

@app.route("/update-location", methods=["POST"])
@login_required
def update_location():
    data = request.get_json()
    lat = data.get("latitude")
    lon = data.get("longitude")
    
    if lat and lon:
        current_user.latitude = lat
        current_user.longitude = lon
        db.session.commit()
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "failed"}), 400


@app.route('/product/<upc>/comment', methods=['POST'])
@login_required
def add_comment(upc):
    content = request.form['content']
    is_public = 'is_public' in request.form
    db.session.add(Comment(user_id=current_user.id, product_upc=upc, content=content, is_public=is_public))
    db.session.commit()

    return redirect(request.referrer or url_for('index'))

@app.route('/product/<upc>/rate', methods=['POST'])
@login_required
def rate(upc):
    score = int(request.form['score'])
    rating = Rating.query.filter_by(user_id=current_user.id, product_upc=upc).first()
    if rating:
        rating.score = score
    else:
        db.session.add(Rating(user_id=current_user.id, product_upc=upc, score=score))
    db.session.commit()
    
    return redirect(request.referrer or url_for('index'))

@app.route('/product/<upc>/favorite', methods=['POST'])
@login_required
def favorite(upc):
    db.session.add(Favorite(user_id=current_user.id, product_upc=upc))
    db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/product/<upc>/unfavorite', methods=['POST'])
@login_required
def unfavorite(upc):
    Favorite.query.filter_by(user_id=current_user.id, product_upc=upc).delete()
    db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/cart_detail/<cart_id>', methods=['POST'])
@login_required
def cart_detail(cart_id):
    return render_template('carts/create_cart.html')

@app.route('/create_cart/<deal_id>', methods=['GET','POST'])
@login_required
def create_cart(deal_id):
    flash("Coming Soon!!")
    return redirect(request.referrer or url_for('index'))
    #return render_template('carts/create_cart.html')



if __name__ == '__main__':
    app.run(debug=True)