from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Product, Rating, Favorite, Comment, ProductStatus, Deal, APIUsage, Message, Group, GroupInvite, Badge, CollectiveCart, CartShare
from functools import wraps
from datetime import datetime, timedelta
import geopy, json, os, random, csv, requests
from markupsafe import Markup
import re
import pandas as pd
from datetime import datetime
from sqlalchemy import func, or_
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from dotenv import load_dotenv
from faker import Faker
from flask_wtf import CSRFProtect
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests.exceptions
from google.cloud import translate_v2 as translate
from langdetect import detect
from io import StringIO
from werkzeug.middleware.proxy_fix import ProxyFix


from helpers import (
    login_required_with_redirect_back,
    evaluate_badge_progress,
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
    seed_badges,
    get_client_ip,
    get_usage,
    enforce_rate_limit,
    get_paginated,
    faker
)

load_dotenv()

#Init server and database connection
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
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


@app.context_processor
def inject_unread_message_count():
    if current_user.is_authenticated:
        unread_count = Message.query.filter_by(receiver_id=current_user.id, read=False).count()
    else:
        unread_count = 0
    return dict(unread_message_count=unread_count)

@app.context_processor
def inject_new_badge():
    new_badge = session.pop('new_badge_earned', None)
    return dict(new_badge=new_badge)

@app.template_filter('nl2br')
def nl2br(value):
    if value is None:
        return ''
    return value.replace('\n', '<br>\n')

@app.template_filter('urlize')
def urlize(value):
    # Very basic url regex
    url_pattern = re.compile(r'(https?://[^\s]+)')
    return Markup(url_pattern.sub(r'<a href="\1" class="text-blue-600 underline" target="_blank">\1</a>', value))

@app.before_request
def initialize_database():
    db.create_all()
    if Badge.query.count() == 0:
        seed_badges()

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
      #seed_users_with_interactions()

    if Deal.query.count() <= 100:
      seed_deals()

@app.before_request
def reset_upc_flag():
    # Only reset for non-API routes (i.e., normal page loads)
    if not request.path.startswith('/api/'):
        session['evaluated_upc'] = False


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
    
    pagination, page, total_pages, total = get_paginated(products, page, per_page=24)
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
    ip = get_client_ip()
    usage = get_usage(ip)
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
        description = request.form['description']
        image_url = request.form['image_url']
        verified_by = request.form['verified_by']
        
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
            description=description,
            image_url=image_url,
            nutriments=nutriments,
            status=status,
            origin='user',
            verified_by=verified_by,
            suggested_by_ip=ip,
            user_id=user_id
        )
        db.session.add(new_product)
        result = evaluate_badge_progress(current_user, 'hunter', increment=1, explicit_progress=len(current_user.products))
        if result:
            session['new_badge_earned'] = result
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

    return render_template('products/new_product.html', upc=upc, usage=usage)

#DEAL DETAIL
@app.route("/deal/<int:deal_id>")
def deal_detail(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    return render_template("deals/deal_detail.html", deal=deal)

#PRODUCT NEW DEAL
@app.route("/product/<string:upc>/new_deal", methods=["GET", "POST"])
def product_new_deal(upc):
    product = Product.query.get_or_404(upc)

    if not current_user.is_authenticated:
        flash('Login Required')
        return redirect(request.referrer or url_for('index'))

    if request.method == "POST":
        store = request.form.get("store")
        if not store:
          store = request.form.get("store-input")
        price_str = request.form.get("price")
        price = float(re.sub(r'[^\d.]', '', price_str)) if price_str else None
        url = request.form.get("url")
        user_id = current_user.id if current_user.is_authenticated else None
        lat = request.form.get("location-lat") or None
        lng = request.form.get("location-lng") or None

        if not store and not url:
         return render_template("products/product_new_deal.html",
          upc=upc, 
          product=product,
          GOOGLE_API_KEY=os.environ.get('GOOGLE_API_KEY'),
          user_lat=current_user.latitude if current_user.is_authenticated else 49.2350654,
          user_lng=current_user.longitude if current_user.is_authenticated else -123.025867
          )
        if price:
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
                db.session.flush() 
                result1 = evaluate_badge_progress(current_user, 'deal_spotter', increment=1, explicit_progress=len(current_user.deals))
                if result1:
                    session['new_badge_earned'] = result1
                users_to_notify = product.favorited_by_to_alert(new_deal)
                result2 = evaluate_badge_progress(current_user, 'megaphone', increment=1, explicit_progress=len(users_to_notify))
                if result2:
                    session['new_badge_earned'] = result2
                for user in users_to_notify:
                    deal_url = url_for('deal_detail', deal_id=new_deal.id, _external=True)
                    msg = Message(
                        sender_id=None,
                        receiver_id=user.id,
                        content=(
                            f"New deal for your favorite product:\n\n {product.name} \n\n"
                            f"Check it out here: {deal_url} \n\n"
                            "This link will open in a new tab.\nEnjoy the savings!"
                            )
                        )
                    db.session.add(msg)
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
      user_lat=current_user.latitude if current_user.is_authenticated else 49.2350654,
      user_lng=current_user.longitude if current_user.is_authenticated else -123.025867
    )

#USER DASHBOARD
@app.route('/dashboard/')
@login_required
def dashboard():
    user_id = current_user.id
    favorites = Favorite.query.filter_by(user_id=user_id).order_by(Favorite.timestamp.desc()).limit(3).all()
    ratings = Rating.query.filter_by(user_id=user_id).order_by(Rating.timestamp.desc()).limit(3).all()
    comments = Comment.query.filter_by(user_id=user_id).order_by(Comment.timestamp.desc()).limit(3).all()
    
    return render_template('dashboard/dashboard.html', favorites=favorites, ratings=ratings, comments=comments)

@app.route('/dashboard/badges')
@login_required
def dashboard_badges():
    badges = Badge.query.all()
    user_badges_dict = {ub.badge_id: ub for ub in current_user.user_badges}
    return render_template("dashboard/badges.html", badges=badges, user_badges_dict=user_badges_dict)

@app.route('/dashboard/added_products')
@login_required
def dashboard_added_products():
    added_products = Product.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/added_products.html', added_products=added_products)

@app.route('/dashboard/deals')
@login_required
def dashboard_deals():
    deals = Deal.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/deals.html', deals=deals)

@app.route('/dashboard/favorites')
@login_required
def dashboard_favorites():
    q = request.args.get('q', '', type=str)
    page = request.args.get('page', 1, type=int)

    query = Favorite.query.filter_by(user_id=current_user.id)

    if q:
        query = query.join(Product).filter(Product.name.ilike(f'%{q}%'))

    favorites = query.order_by(Favorite.id.desc()).paginate(page=page, per_page=12)
    
    def url_builder(p):
        return url_for('dashboard_favorites', page=p)
    return render_template('dashboard/favorites.html', favorites=favorites, url_builder=url_builder)

@app.route('/dashboard/groups')
@login_required
def dashboard_groups():
    memberships = GroupInvite.query.filter_by(user_id=current_user.id, accepted=True, deleted=False).all()
    group_invites = GroupInvite.query.filter_by(user_id=current_user.id, accepted=False, deleted=False).all()

    # Get accepted group objects
    group_ids = [invite.group_id for invite in memberships]
    groups = Group.query.filter(Group.id.in_(group_ids)).filter_by(deleted=False).all()

    return render_template('dashboard/groups.html', groups=groups, group_invites=group_invites)

@app.route('/dashboard/carts')
@login_required
def dashboard_carts():

    carts = CollectiveCart.query.filter_by(host_id=current_user.id, deleted=False).all()

    shares = CartShare.query.filter_by(user_id=current_user.id, deleted=False).all()
    shares_count = {}
    for share in shares:
        if share.cart.id in shares_count:
            shares_count[share.cart_id] += 1
        else:
            shares_count[share.cart_id] = 1

    unique_shares = {}
    for share in shares:
        cart_id = share.cart_id
        if cart_id not in unique_shares and share.cart.host.id is not current_user.id:
            unique_shares[cart_id] = share



    return render_template('dashboard/carts.html', carts=carts, shares=unique_shares, shares_count=shares_count)

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
    if not current_user.admin:
        return redirect(url_for("dashboard"))
        flash("Must be an Admin",'error')

    suggestions = Product.query.filter_by(status=ProductStatus.SUGGESTED).all()
    api_usage = APIUsage.query.all()

    api_calls = 0
    for use in api_usage:
        api_calls += use.count
    return render_template("dashboard/suggestions.html", products=suggestions, api_calls=api_calls)

@app.route("/suggestion/approve/<product_upc>", methods=["POST"])
@login_required
def approve_suggestion(product_upc):
    if not current_user.admin:
        return redirect(url_for("dashboard"))
        flash("Must be an Admin",'error')

    user_id = current_user.id
    product = Product.query.get_or_404(product_upc)
    product.status = ProductStatus.APPROVED
    product.user_id = user_id
    db.session.commit()
    flash(f"Product '{product.name}' approved.", "success")
    return redirect(url_for("dashboard_suggestions"))

@app.route("/dashboard/reports")
@login_required
def dashboard_reports():
    if not current_user.admin:
        flash("Must be an Admin", 'error')
        return redirect(url_for("dashboard"))

    # Flagged Products: products with 0-rated scores
    flagged_products = (
        db.session.query(
            Product,
            func.count(Rating.id).label('flag_count')
        )
        .join(Rating, Rating.product_upc == Product.upc)
        .filter(Rating.score == 0)
        .group_by(Product)
        .order_by(func.count(Rating.id).desc())
        .all()
    )

    # Flagged Deals: deals with 0-rated scores
    flagged_deals = (
        db.session.query(
            Deal,
            func.count(Rating.id).label('flag_count')
        )
        .join(Rating, Rating.deal_id == Deal.id)
        .filter(Rating.score == 0)
        .group_by(Deal)
        .order_by(func.count(Rating.id).desc())
        .all()
    )

    # Already have full objects in results (Product, Deal), no need to re-fetch
    product_flags = [
        {'product': product, 'flag_count': flag_count}
        for product, flag_count in flagged_products
    ]

    deal_flags = [
        {'deal': deal, 'flag_count': flag_count}
        for deal, flag_count in flagged_deals
    ]

    return render_template(
        'dashboard/reports.html',
        flagged_products=product_flags,
        flagged_deals=deal_flags
    )


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
def discover():
    if not session.get("latitude") or not session.get("longitude"):
        flash("Location services are required to use Discovery.", "warning")
        return redirect(url_for("home"))  # or your homepage route name

    tab = request.args.get('tab', 'deals')  # default to 'deals'
    page = request.args.get('page', 1, type=int)

    total_pages = 1
    total_display = 0

    similar_users  = []
    recommended_products = []
    potential_groups = []
    nearby_users = []
    nearby_deals = []

    def url_builder(p):
        return url_for('discover', tab=tab, page=p)
    
    if tab != 'deals':
        if not current_user.is_authenticated:
            flash('Must be logged in!','warning')
            return redirect(url_for('discover'))

        user = current_user
        user_favorites = get_user_favorites(user.id)
        user_ratings = get_user_ratings(user.id)
        user_lat = current_user.latitude 
        user_lng = current_user.longitude

        if tab == 'users':
            if not current_user.admin:
                flash('Must be an admin!','warning')
                return redirect(url_for('discover'))
            similar_users = find_similar_users(current_user.id)
            similar_users, pages, total_pages, total_display = get_paginated(similar_users, page, per_page=12)
        if tab == 'products':
            similar_users = find_similar_users(current_user.id)
            recommended_products = recommend_products(user, other_users=similar_users)
            recommended_products, pages, total_pages, total_display = get_paginated(recommended_products, page, per_page=12)
        if tab == 'groups':
            potential_groups = get_potential_groups(user)
            potential_groups, pages, total_pages, total_display = get_paginated(potential_groups, page, per_page=12)
    else:
        if current_user.is_authenticated:
            nearby_users = current_user.nearby_users(radius_km=40)
            nearby_deals = current_user.nearby_deals(radius_km=40)
            nearby_deals, pages, total_pages, total_display = get_paginated(nearby_deals, page, per_page=60)
            user_lat = current_user.latitude 
            user_lng = current_user.longitude
        else:
            nearby_users = ''
            nearby_deals = Deal.query.all()
            nearby_deals, pages, total_pages, total_display = get_paginated(nearby_deals, page, per_page=60)
            user_lat = 49.2350654 
            user_lng = -123.025867

    
    return render_template('discover.html',
                           similar_users=similar_users,
                           total_display=total_display,
                           tab=tab,
                           page=page, 
                           total_pages=total_pages,
                           user_lat=user_lat,
                           user_lng=user_lng,
                           recommended_products=recommended_products,
                           nearby_users=nearby_users,
                           potential_groups=potential_groups,
                           nearby_deals=nearby_deals,
                           url_builder=url_builder)


@app.route('/api/check_upc/<upc>')
def check_upc(upc):
    product = Product.query.filter_by(upc=upc).first()

    if current_user.is_authenticated and not session.get('evaluated_upc'):
        session['evaluated_upc'] = True  # Prevent further increments this scan
        result = evaluate_badge_progress(current_user, 'scanner', increment=1)
        if result:
            session['new_badge_earned'] = result

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
    ip = get_client_ip()
    usage = get_usage(ip)
    rate_limit_response = enforce_rate_limit(usage, ip)
    if rate_limit_response:
        return jsonify({"error": "Rate limit exceeded"}), 404
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
    upcdb_response = requests.get(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={upc}",
        headers={"Content-Type": "application/json"}
    )

    if upcdb_response.ok:
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


    query = request.args.get("q", "")
    users = []
    users = User.query.filter(
            (User.username.ilike(f"%{query}%")) | (User.email.ilike(f"%{query}%"))
            ).all()

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
                password=generate_password_hash(password),
                admin=is_admin
            )
            db.session.add(user)
            db.session.commit()
            flash(f'User {username} created successfully.', 'success')
            return redirect(url_for('dashboard_new_user'))
    messages_all = Message.query.all()
    return render_template('dashboard/new_user.html', users=users, query=query, messages_all=messages_all)

@app.route('/delete-user', methods=['POST'])
@login_required
def delete_user():
    if not current_user.admin:
        return redirect(url_for('dashboard'))

    user_id = request.form.get("user_id")
    confirm_password = request.form.get("confirm_password")

    if not current_user.check_password(confirm_password):
        flash("Incorrect password. Deletion cancelled.", "danger")
        return redirect(url_for("dashboard_new_user"))

    user = User.query.get(user_id)
    if user:
        db.session.delete(user)
        db.session.commit()
        flash(f"User {user.username} deleted.", "success")
    else:
        flash("User not found.", "warning")

    return redirect(url_for("dashboard_new_user"))

@app.route('/user_detail/<user_id>')
@login_required
def user_detail(user_id):
    user = User.query.filter_by(id=user_id).first()
    return render_template("user/user_detail.html", user=user)

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


        if current_user.city is None or current_user.country is None:
            geolocator = Nominatim(user_agent="shopidarity")
            location = geolocator.reverse(f"{lat}, {lon}", language='en')
            address = location.raw.get('address', {})
            city = (address.get('city') or address.get('town') or address.get('village') or '').strip().title()
            country = (address.get('country') or '').strip().title()
            current_user.city, current_user.country = city, country

        db.session.commit()

    if lat is not None and lon is not None:
        session["latitude"] = lat
        session["longitude"] = lon
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

    ratings = Rating.query.filter_by(user_id=current_user.id).all()

    result = evaluate_badge_progress(current_user, 'opinion', increment=1, explicit_progress=len(ratings))
    if result:
        session['new_badge_earned'] = result

    
    return redirect(request.referrer or url_for('index'))

@app.route('/deal/<deal_id>/rate', methods=['POST'])
@login_required
def rate_deal(deal_id):
    score = int(request.form['score'])
    rating = Rating.query.filter_by(user_id=current_user.id, deal_id=deal_id).first()
    if rating:
        rating.score = score
    else:
        db.session.add(Rating(user_id=current_user.id, deal_id=deal_id, score=score))
    db.session.commit()
    
    return redirect(request.referrer or url_for('index'))

@app.route('/product/<upc>/favorite', methods=['POST'])
@login_required
def favorite(upc):
    if len(current_user.favorites) >= current_user.max_favorites:
        flash('Favorite limit reached.', 'error')
        return redirect(request.referrer or url_for('index'))
    product = Product.query.get_or_404(upc)

    db.session.add(Favorite(user_id=current_user.id, product_upc=upc, to_alert=current_user.default_fav_alert))
    result =evaluate_badge_progress(current_user, 'pantry', increment=1, explicit_progress=len(current_user.favorites))
    if result:
        session['new_badge_earned'] = result
    if product.user and product.user.id != current_user.id:
        fav_count = db.session.query(Favorite).join(Product).filter(Product.user_id == product.user.id).count()

        send_msg =evaluate_badge_progress(product.user, 'trend_starter', increment=1, explicit_progress=fav_count)
        if send_msg:
            message_text = (
                f"🔥 Your product is trending! You've unlocked a new level in your **Trend Starter** badge: "
                f"**{send_msg['level']}**.\n\n"
                "That means your product has been favorited by others — you're setting the trend! 💫"
            )

            db.session.add(Message(receiver_id=product.user.id, content=message_text))
    db.session.commit()

    return redirect(request.referrer or url_for('index'))

@app.route('/product/<upc>/unfavorite', methods=['POST'])
@login_required
def unfavorite(upc):
    Favorite.query.filter_by(user_id=current_user.id, product_upc=upc).delete()
    db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/favorites/<int:favorite_id>/toggle_alert', methods=['POST'])
@csrf.exempt
@login_required
def toggle_favorite_alert(favorite_id):
    favorite = Favorite.query.filter_by(id=favorite_id, user_id=current_user.id).first_or_404()
    favorite.to_alert = not favorite.to_alert
    db.session.commit()
    return jsonify({'to_alert': favorite.to_alert})

@app.route('/users/default_alert', methods=['POST'])
@csrf.exempt
@login_required
def update_default_alert():
    data = request.get_json()
    if not data or 'default_fav_alert' not in data:
        return jsonify({'error': 'Invalid request data'}), 400

    try:
        current_user.default_fav_alert = bool(data['default_fav_alert'])
        db.session.commit()
        return jsonify({'success': True, 'default_fav_alert': current_user.default_fav_alert})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Server error updating setting'}), 500

@app.route('/settings/update-deal-distance', methods=['POST'])
@csrf.exempt
@login_required
def update_deal_distance():
    try:
        data = request.get_json()
        distance = data.get('deal_distance', None)
        if distance is None:
            return jsonify({"error": "Missing distance"}), 400
        distance = float(distance)
        if distance < 0:
            return jsonify({"error": "Invalid distance"}), 400

        current_user.deal_distance = distance
        db.session.commit()

        return jsonify({"success": True, "deal_distance": distance})

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/deal/<deal_id>/carts')
@login_required
def deal_carts(deal_id):
    deal = Deal.query.filter_by(id=deal_id).first()
    return render_template('deals/deal_carts.html', deal=deal)


from flask import request, redirect, flash, url_for

@app.route('/cart/<int:cart_id>', methods=['GET', 'POST'])
@login_required
def cart_detail(cart_id):
    cart = CollectiveCart.query.get_or_404(cart_id)

    if request.method == 'POST':
        # Has the user already requested or claimed a share?
        existing_user_share = CartShare.query.filter_by(cart_id=cart.id, user_id=current_user.id, deleted=False).first()
        if existing_user_share:
            flash('You already have a share request or claim in this cart.', 'info')
        else:
            # Find first unclaimed share (i.e. placeholder)
            available_share = CartShare.query.filter_by(cart_id=cart.id, user_id=None, deleted=False).first()

            if available_share:
                available_share.user_id = current_user.id
                available_share.approved = False  # Host will later approve
                db.session.commit()
                flash('Your share request has been submitted. Awaiting host approval.', 'success')
            else:
                flash('No available shares remain in this cart.', 'danger')

        return redirect(url_for('cart_detail', cart_id=cart.id))

    return render_template('carts/cart_detail.html', cart=cart, user=current_user)



@app.route('/create_cart/<int:deal_id>', methods=['GET', 'POST'])
@login_required
def create_cart(deal_id):
    deal = Deal.query.get_or_404(deal_id)

    if request.method == 'POST':
        try:
            # Extract form values
            add_tax = request.form.get('add_tax') == 'yes'
            tax_rate = float(request.form.get('tax_rate') or 0)
            share_count = int(request.form.get('share_count') or 1)
            MAX_SHARES = 25
            if share_count > MAX_SHARES:
                flash(f"Share count cannot exceed {MAX_SHARES}", "danger")
                return redirect(request.referrer or url_for('index'))
            privacy = request.form.get('privacy')
            pickup_date_str = request.form.get('pickup_date')
            print(pickup_date_str)
            pickup_date = datetime.strptime(pickup_date_str, '%Y-%m-%d').date() if pickup_date_str else None
            pickup_time_str = request.form.get('pickup_time')
            print(pickup_time_str)
            pickup_time = datetime.strptime(pickup_time_str, '%H:%M').time() if pickup_time_str else None
            payment_timing = request.form.get('payment_timing')
            payment_method = request.form.get('payment_method')
            description = request.form.get('host_notes')
            host_shares_raw = request.form.get('host_shares')
            host_shares = int(host_shares_raw) if host_shares_raw and host_shares_raw.isdigit() else 1
            if host_shares > share_count:
                flash(f"Share Host Shares cannot exceed Share Count", "danger")
                return redirect(request.referrer or url_for('index'))

            max_shares_raw = request.form.get('max_shares_per_user')
            max_shares = int(max_shares_raw) if max_shares_raw and max_shares_raw.isdigit() else None


            # Compute total cost
            base_price = deal.price
            if add_tax:
                total_cost = round(base_price * (1 + tax_rate / 100), 2)
            else:
                total_cost = base_price

            # Create the cart

            new_cart = CollectiveCart(
                deal_id=deal.id,
                host_id=current_user.id,
                total_cost=total_cost,
                share_count=share_count,
                latitude=deal.latitude,
                longitude=deal.longitude,
                store_name=deal.store,
                expiry=deal.expiry if deal.on_sale else None,
                privacy=privacy,
                payment_timing=payment_timing,
                payment_method=payment_method,
                description=description,
                max_shares=max_shares,
                pickup_date=pickup_date,
                pickup_time=pickup_time
                )

            db.session.add(new_cart)
            db.session.flush()  # get cart.id before creating shares

            # Create CartShares
            for i in range(share_count):
                is_host = i < host_shares
                user_id = current_user.id if is_host else None  # host is first, others are empty for now
                approved = True if is_host else False  # host is first, others are empty for now

                share = CartShare(
                    cart_id=new_cart.id,
                    user_id=user_id,
                    fulfilled=False,
                    approved=approved,
                    deleted=False
                )
                db.session.add(share)

            db.session.commit()

            flash("Cart created successfully!", "success")
            return redirect(url_for('cart_detail', cart_id=new_cart.id))

        except Exception as e:
            db.session.rollback()
            flash(f"Error creating cart: {str(e)}", "danger")
            return redirect(request.referrer or url_for('index'))

    return render_template('carts/create_cart.html', deal=deal)

@app.route('/approve_share/<int:share_id>', methods=['POST'])
@login_required
def approve_share(share_id):
    share = CartShare.query.get_or_404(share_id)
    if current_user.id != share.cart.host_id:
        abort(403)
    share.approved = True
    db.session.commit()
    flash('Share approved.')
    return redirect(url_for('cart_detail', cart_id=share.cart.id))


@app.route('/reject_share/<int:share_id>', methods=['POST'])
@login_required
def reject_share(share_id):
    share = CartShare.query.get_or_404(share_id)
    if current_user.id != share.cart.host_id:
        abort(403)
    share.user_id = None
    db.session.commit()
    flash('Share rejected and removed.')
    return redirect(url_for('cart_detail', cart_id=share.cart.id))


@app.route("/faq")
def faq():
    return render_template("faq.html")

@app.route("/tos")
def toss():
    return render_template("ToS.html")

@app.route('/download_user_products')
def download_user_products():
    products = Product.query.filter_by(origin='user').all()

    si = StringIO()
    writer = csv.writer(si)

    # Write header
    writer.writerow([
        'UPC', 'Name', 'Category', 'Brand', 'Description', 'Image URL',
        'Nutriments', 'Origin', 'Verified By', 'Status', 'User ID'
    ])

    for p in products:
        writer.writerow([
            p.upc,
            p.name,
            p.category,
            p.brand,
            p.description,
            p.image_url,
            str(p.nutriments) if p.nutriments else '',
            p.origin,
            p.verified_by,
            p.status.name if p.status else '',
            p.user_id
        ])

    output = si.getvalue()
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=user_products.csv'}
    )

# View inbox (messages received by current user)
@app.route('/messages')
@login_required
def messages_inbox():
    messages = Message.query.filter_by(receiver_id=current_user.id).order_by(Message.sent_at.desc()).all()
    return render_template('messages/inbox.html', messages=messages)

# View a single message
@app.route('/messages/<int:message_id>')
@login_required
def message_detail(message_id):
    message = Message.query.get_or_404(message_id)
    # Make sure the user can only see messages sent to or from them
    if message.receiver_id != current_user.id and message.sender_id != current_user.id:
        flash("You don't have permission to view this message.", "danger")
        return redirect(url_for('messages_inbox'))

    if not message.read:
        message.read = True
        db.session.commit()

    return render_template('messages/detail.html', message=message)

# Create a message (sending)
@app.route('/messages/create', methods=['GET', 'POST'])
@login_required
def message_create():
    if request.method == 'POST':
        receiver_username = request.form.get('receiver_username')
        content = request.form.get('content')

        receiver = User.query.filter_by(username=receiver_username).first()
        if not receiver:
            flash("Receiver not found.", "danger")
            return redirect(url_for('message_create'))

        if not content:
            flash("Message content cannot be empty.", "danger")
            return redirect(url_for('message_create'))

        msg = Message(sender_id=current_user.id, receiver_id=receiver.id, content=content)
        db.session.add(msg)
        db.session.commit()
        flash("Message sent!", "success")
        return redirect(url_for('messages_inbox'))

    return render_template('messages/create.html')

# Delete a message (only if current user is sender or receiver)
@app.route('/messages/<int:message_id>/delete', methods=['POST'])
@login_required
def message_delete(message_id):
    message = Message.query.get_or_404(message_id)
    if message.receiver_id != current_user.id and message.sender_id != current_user.id:
        flash("You don't have permission to delete this message.", "danger")
        return redirect(url_for('messages_inbox'))

    db.session.delete(message)
    db.session.commit()
    flash("Message deleted.", "success")
    return redirect(url_for('messages_inbox'))

#GROUPS
@app.route('/invite/<int:invite_id>/accept', methods=['POST'])
@login_required
def accept_invite(invite_id):
    invite = GroupInvite.query.get_or_404(invite_id)

    if invite.user_id != current_user.id or invite.deleted:
        flash("Invalid or expired invite.", "danger")
        return redirect(url_for('dashboard'))

    invite.accepted = True
    invite.deleted = False  # Ensure undeleted if already soft-declined
    db.session.commit()

    flash("You've joined the group!", "success")
    return redirect(url_for('groups_detail', group_id=invite.group_id))


@app.route('/invite/<int:invite_id>/decline', methods=['POST'])
@login_required
def decline_invite(invite_id):
    invite = GroupInvite.query.get_or_404(invite_id)

    if invite.user_id != current_user.id or invite.deleted:
        flash("Invalid or expired invite.", "danger")
        return redirect(url_for('dashboard_groups'))

    invite.deleted = True
    invite.accepted = False  # Ensure not interpreted as a member
    db.session.commit()

    flash("You've declined the group invitation.", "info")
    return redirect(url_for('dashboard_groups'))

@app.route('/api/overlapping_groups', methods=['GET'])
@login_required
def get_overlapping_groups():
    try:
        product_id = int(request.args.get('product_id'))
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid input"}), 400

    user_coords = (lat, lon)
    overlapping = []

    groups = Group.query.filter_by(product_upc=product_id).all()
    for group in groups:
        group_coords = (group.center_lat, group.center_lon)
        distance_km = geodesic(user_coords, group_coords).km

        if distance_km <= group.radius_km:
            overlapping.append({
                "id": group.id,
                "name": group.name,
                "distance_km": round(distance_km, 2),
                "radius_km": group.radius_km,
                "center_lat": round(group.center_lat, 2),
                "center_lon": round(group.center_lon, 2),
            })

    return jsonify(overlapping)

@app.route('/groups/review', methods=['POST'])
@login_required
def review_group():
    product_upc = request.form.get('product_upc')
    user_ids_json = request.form.get('user_ids')
    try:
        user_ids = json.loads(user_ids_json)
    except (TypeError, json.JSONDecodeError):
        flash("Invalid user data.", "error")
        return redirect(url_for('home'))  # or wherever fallback is appropriate

    product = Product.query.get_or_404(product_upc)
    users = User.query.filter(User.id.in_(user_ids)).all()

    return render_template('groups/review_group.html', product=product, users=users)


@app.route('/group/create', methods=['POST'])
@login_required
def create_group():
    product_upc = request.form.get('product_upc')
    name = request.form.get('group_name')

    center_lat = request.form.get('location_lat')
    center_lon = request.form.get('location_lng')
    radius_km = request.form.get('radius_km')

    selected_user_ids = request.form.getlist('user_ids')  # from checkbox values

    group = Group(product_upc=product_upc, organizer_id=current_user.id, name=name, center_lat=center_lat,center_lon=center_lon,radius_km=radius_km)
    db.session.add(group)
    db.session.flush()

    invitation = GroupInvite(group_id=group.id, user_id=current_user.id, accepted=True)
    db.session.add(invitation)
    
    for user_id in selected_user_ids:
        invitation = GroupInvite(group_id=group.id, user_id=user_id)
        db.session.add(invitation)
        message = Message(
                        sender_id=None,
                        receiver_id=user_id,
                        content=(
                            f"New Goroup Invitation! \n\n" 
                            f"Check it out in your dashboard."
                            )
                        )
        db.session.add(message)

    db.session.commit()
    flash("Group created and invitations sent!", "success")
    return redirect(url_for('group_detail', group_id=group.id))

@app.route('/group/<int:group_id>')
@login_required
def group_detail(group_id):
    group = Group.query.get_or_404(group_id)
    product = group.product
    organizer = User.query.get_or_404(group.organizer_id)  # assuming relationship Group.organizer points to User
    tab = request.args.get('tab', 'bulletin')  # default to 'bulletin'

    bulletin_posts = []  
    deals = []          
    carts = []          
    # Accepted members (assuming GroupInvitation has accepted boolean)
    accepted_invitations = GroupInvite.query.filter_by(group_id=group.id, accepted=True).all()
    accepted_users = [inv.user for inv in accepted_invitations]

    # Pending invitations
    pending_invitations = GroupInvite.query.filter_by(group_id=group.id, accepted=False).all()
    pending_users = [inv.user for inv in pending_invitations]

      # Fetch the relevant data based on the selected tab for performance optimization (optional)
    if tab == 'bulletin':
        bulletin_posts = []  # fetch bulletin posts for group
        #bulletin_posts = get_bulletin_posts(group_id)
    elif tab == 'deals':
        for user in accepted_users:
            user_deals = Deal.query.filter_by(user_id=user.id, product_id=product.upc).all()
            deals.extend(user_deals)
        for user in accepted_users:
            user_carts = (
                db.session.query(CollectiveCart)
                .join(Deal, CollectiveCart.deal_id == Deal.id)
                .filter(
                    CollectiveCart.host_id == user.id,
                    Deal.product_id == product.upc
                    )
                .all()
                )
            carts.extend(user_carts)


    return render_template(
        'groups/group_detail.html',
        group=group,
        product=product,
        organizer=organizer,
        accepted_users=accepted_users,
        deals=deals,
        carts=carts,
        pending_users=pending_users,
        bulletin_posts=bulletin_posts,
        tab=tab
    )


if __name__ == '__main__':
    app.run(debug=True)