from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from functools import wraps
import os
import traceback
from io import BytesIO
from weasyprint import HTML
from sqlalchemy import or_, and_, select, func, desc
from collections import Counter
import logging
from logging.handlers import RotatingFileHandler

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_super_duper_secret_key_for_wishlist_v6' # CHANGE THIS!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hotel_booking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- Logging Setup ---
if not os.path.exists('logs'):
    os.mkdir('logs')
file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('HotelApp startup')


# --- Upload Configuration ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'hotel_images')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        app.logger.info(f"Upload folder checked/created: {UPLOAD_FOLDER}")
    except OSError as e:
        app.logger.error(f"Error creating upload folder {UPLOAD_FOLDER}: {e}")

db = SQLAlchemy(app)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(int(user_id))
    if user and user.is_active:
        return user
    return None

# --- Database Models (Correct Order) ---
hotel_amenities = db.Table('hotel_amenities',
    db.Column('hotel_id', db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), primary_key=True),
    db.Column('amenity_id', db.Integer, db.ForeignKey('amenity.id', ondelete='CASCADE'), primary_key=True)
)

class Amenity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class HotelImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

class RoomImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(255), nullable=False)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hotels_owned = db.relationship('Hotel', backref='owner_user', lazy=True, foreign_keys='Hotel.owner_id', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='booker', lazy=True, foreign_keys='Booking.user_id', cascade="all, delete-orphan")
    wishlist_items = db.relationship('Wishlist', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    audit_logs = db.relationship('AuditLog', backref='actor', lazy=True, foreign_keys='AuditLog.user_id')

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class RoomType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=False)
    number_of_rooms = db.Column(db.Integer, nullable=False, default=1)
    amenities = db.Column(db.String(300), nullable=True)
    main_image_url = db.Column(db.String(255), nullable=True, default='https://via.placeholder.com/800x500.png?text=Room+Image')
    gallery_images = db.relationship('RoomImage', backref='room_type_ref', lazy='dynamic', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='room_type', lazy='dynamic', cascade="all, delete-orphan")
    availabilities = db.relationship('Availability', backref='room_type_ref', lazy='dynamic', cascade="all, delete-orphan")
    def get_amenities_list(self): return [a.strip() for a in self.amenities.split(',') if a.strip()] if self.amenities else []

class Hotel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=True)
    image_url = db.Column(db.String(255), nullable=True, default='https://via.placeholder.com/800x500.png?text=Main+Hotel+Image')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    star_rating = db.Column(db.Integer, nullable=True) 
    amenities = db.relationship('Amenity', secondary=hotel_amenities, lazy='subquery', backref=db.backref('hotels', lazy=True))
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    gallery_images = db.relationship('HotelImage', backref='hotel_ref', lazy='dynamic', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='hotel', lazy=True, foreign_keys='Booking.hotel_id', cascade="all, delete-orphan")
    wishlisted_by_users = db.relationship('Wishlist', backref='hotel', lazy='dynamic', cascade='all, delete-orphan')
    room_types = db.relationship('RoomType', backref='hotel', lazy='dynamic', cascade="all, delete-orphan")
    def get_amenities_list(self): return [amenity.name for amenity in self.amenities]
    def get_min_price(self): return db.session.query(func.min(RoomType.price_per_night)).filter(RoomType.hotel_id == self.id).scalar()
    def update_min_price(self): self.price_per_night = self.get_min_price(); db.session.add(self)
    def to_dict_for_map(self):
        min_price = self.get_min_price()
        return {'name': self.name, 'lat': self.latitude, 'lng': self.longitude, 'url': url_for('hotel_detail', hotel_id=self.id), 'imageUrl': self.image_url, 'price': "{:.2f}".format(min_price) if min_price else "N/A", 'location': self.location}

class Availability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id', ondelete='CASCADE'), nullable=False)
    price = db.Column(db.Float, nullable=True)
    available_rooms = db.Column(db.Integer, nullable=True)
    __table_args__ = (db.UniqueConstraint('date', 'room_type_id', name='_date_room_uc'),)

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id', ondelete='CASCADE'), nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    check_out_date = db.Column(db.Date, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Confirmed')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Wishlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    added_on = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'hotel_id', name='_user_hotel_wishlist_uc'),)

# --- Helper Functions & Decorators ---
def log_audit(action):
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        log_entry = AuditLog(user_id=user_id, action=action)
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        app.logger.error(f"Failed to log audit action '{action}': {e}")
        db.session.rollback()

def role_required(role_name):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            roles = role_name if isinstance(role_name, list) else [role_name]
            if current_user.role not in roles:
                flash(f"Access Denied: You need one of the following roles: {', '.join(roles)}.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def verify_hotel_owner(f):
    @wraps(f)
    def decorated_function(hotel_id, *args, **kwargs):
        hotel = Hotel.query.get_or_404(hotel_id)
        if current_user.role == 'owner' and hotel.owner_id != current_user.id:
            flash("You are not authorized to manage this hotel.", "danger")
            return redirect(url_for('owner_dashboard'))
        return f(hotel, *args, **kwargs)
    return decorated_function

@app.context_processor
def utility_processor():
    def is_hotel_in_wishlist(hotel_id):
        if current_user.is_authenticated:
            return Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel_id).first() is not None
        return False
    return dict(is_hotel_in_wishlist=is_hotel_in_wishlist)

# --- Core Logic for Availability ---
def get_daily_details(room_type, target_date):
    override = Availability.query.filter_by(room_type_id=room_type.id, date=target_date).first()
    price = override.price if override and override.price is not None else room_type.price_per_night
    available = override.available_rooms if override and override.available_rooms is not None else room_type.number_of_rooms
    return {'price': price, 'available': available}

def check_availability_and_calculate_price(room_type_id, check_in_date, check_out_date):
    room_type = RoomType.query.get_or_404(room_type_id)
    total_price = 0
    current_date = check_in_date
    while current_date < check_out_date:
        daily_info = get_daily_details(room_type, current_date)
        if daily_info['available'] <= 0:
             return {'available': False, 'message': f"No rooms available on {current_date.strftime('%Y-%m-%d')}.", 'price': 0}
        booked_on_date = Booking.query.filter(
            Booking.room_type_id == room_type_id, Booking.check_in_date <= current_date, Booking.check_out_date > current_date
        ).count()
        if booked_on_date >= daily_info['available']:
            return {'available': False, 'message': f"Fully booked on {current_date.strftime('%Y-%m-%d')}.", 'price': 0}
        total_price += daily_info['price']
        current_date += timedelta(days=1)
    return {'available': True, 'message': 'Available', 'price': total_price}

# --- Main Routes ---
@app.route('/')
def index():
    query = Hotel.query.filter_by(is_approved=True)
    search_term = request.args.get('search_term', '').strip()
    amenities_filter_str = request.args.get('amenities_filter', '').strip()
    if search_term:
        query = query.filter(or_(Hotel.name.ilike(f"%{search_term}%"), Hotel.location.ilike(f"%{search_term}%")))
    if amenities_filter_str:
        req_amenities = [a.strip().lower() for a in amenities_filter_str.split(',') if a.strip()]
        for amenity in req_amenities:
            query = query.join(Hotel.amenities).filter(Amenity.name.ilike(f'%{amenity}%'))
    filtered_hotels = query.order_by(Hotel.name.asc()).all()
    return render_template('index.html', hotels=filtered_hotels)

# --- Auth & Profile Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'user')
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger'); return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger'); return redirect(url_for('register'))
        new_user = User(username=username, email=email, role=role, is_active=True)
        new_user.set_password(password)
        db.session.add(new_user); db.session.commit()
        log_audit(f"User '{username}' registered as '{role}'.")
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been suspended. Please contact an administrator.', 'danger')
                log_audit(f"Failed login attempt for suspended user: {username}")
                return redirect(url_for('login'))
            login_user(user, remember=request.form.get('remember_me'))
            log_audit(f"User '{user.username}' logged in successfully.")
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            if user.role == 'admin': return redirect(next_page or url_for('admin_dashboard'))
            elif user.role == 'owner': return redirect(next_page or url_for('owner_dashboard'))
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid username or password.', 'danger')
            log_audit(f"Failed login attempt for username: {username}")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_audit(f"User '{current_user.username}' logged out.")
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        user = current_user
        new_email = request.form.get('email', '').strip()
        if new_email and new_email != user.email:
            existing_user = User.query.filter(and_(User.email == new_email, User.id != user.id)).first()
            if existing_user: flash('That email address is already in use.', 'danger')
            else: user.email = new_email; log_audit(f"User '{user.username}' updated their email."); flash('Your email has been updated.', 'success')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        if new_password:
            if not user.check_password(current_password): flash('Your current password is not correct.', 'danger')
            elif new_password != request.form.get('confirm_password'): flash('The new passwords do not match.', 'danger')
            else: user.set_password(new_password); log_audit(f"User '{user.username}' updated their password."); flash('Your password has been updated.', 'success')
        db.session.commit()
        return redirect(url_for('profile'))
    recent_bookings = Booking.query.filter_by(user_id=current_user.id).order_by(desc(Booking.created_at)).limit(5).all()
    return render_template('profile.html', user=current_user, bookings=recent_bookings)

# --- Hotel & Booking Routes ---
@app.route('/hotel/<int:hotel_id>')
def hotel_detail(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    if not hotel.is_approved and (not current_user.is_authenticated or (current_user.role != 'admin' and current_user.id != hotel.owner_id)):
        flash("This hotel is not yet approved or does not exist.", "warning")
        return redirect(url_for('index'))
    room_types = hotel.room_types.order_by(RoomType.price_per_night.asc()).all()
    return render_template('hotel_detail.html', hotel=hotel, room_types=room_types, today=date.today().isoformat())

@app.route('/book_room/<int:room_type_id>', methods=['POST'])
@login_required
def book_room(room_type_id):
    room_type = RoomType.query.get_or_404(room_type_id)
    hotel = room_type.hotel
    check_in_str = request.form.get('check_in_date')
    check_out_str = request.form.get('check_out_date')
    try:
        check_in_date_obj = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        check_out_date_obj = datetime.strptime(check_out_str, '%Y-%m-%d').date()
        if check_out_date_obj <= check_in_date_obj: raise ValueError
    except (ValueError, TypeError):
        flash("Invalid date format or range.", "danger")
        return redirect(url_for('hotel_detail', hotel_id=hotel.id))
    result = check_availability_and_calculate_price(room_type_id, check_in_date_obj, check_out_date_obj)
    if not result['available']:
        flash(result['message'], 'danger')
        return redirect(url_for('hotel_detail', hotel_id=hotel.id))
    booking = Booking(
        user_id=current_user.id, hotel_id=hotel.id, room_type_id=room_type_id,
        check_in_date=check_in_date_obj, check_out_date=check_out_date_obj, total_price=result['price']
    )
    db.session.add(booking); db.session.commit()
    log_audit(f"User '{current_user.username}' booked room '{room_type.name}' at hotel '{hotel.name}' (Booking ID: {booking.id}).")
    flash(f'Successfully booked the {room_type.name}! Your booking is confirmed.', 'success')
    return redirect(url_for('booking_confirmed', booking_id=booking.id))

@app.route('/booking_confirmed/<int:booking_id>')
@login_required
def booking_confirmed(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and current_user.role != 'admin':
        flash("Unauthorized access.", "danger"); return redirect(url_for('index'))
    return render_template('booking_confirmed.html', booking=booking)

@app.route('/download_receipt/<int:booking_id>')
@login_required
def download_receipt(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and current_user.role != 'admin':
        flash("Unauthorized.", "danger"); return redirect(url_for('my_bookings'))
    html_string = render_template('receipt_template.html', booking=booking, user=booking.booker)
    pdf_bytes = HTML(string=html_string).write_pdf()
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=receipt_booking_{booking.id}.pdf'
    return response

@app.route('/my_bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(desc(Booking.created_at)).all()
    return render_template('my_bookings.html', bookings=bookings)

# --- Owner Routes ---
@app.route('/register_hotel', methods=['GET', 'POST'])
@role_required('owner')
def register_hotel():
    all_amenities = Amenity.query.order_by(Amenity.name).all()
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        main_image_url = request.form.get('main_image_url', '').strip()

        if not name or not location:
            flash("Hotel Name and Location are required.", "danger")
            return render_template('register_hotel.html', form_data=request.form, all_amenities=all_amenities)

        new_hotel = Hotel(
            name=name, location=location, owner_id=current_user.id, is_approved=False,
            description=request.form.get('description'),
            star_rating=int(request.form.get('star_rating')) if request.form.get('star_rating') else None
        )
        
        selected_amenity_ids = request.form.getlist('amenities')
        new_hotel.amenities = Amenity.query.filter(Amenity.id.in_(selected_amenity_ids)).all()
        db.session.add(new_hotel)
        db.session.flush()

        uploaded_files = request.files.getlist('gallery_images_upload')
        image_filenames_saved = []
        for i, file_obj in enumerate(uploaded_files):
            if file_obj and allowed_file(file_obj.filename):
                original_fn = secure_filename(file_obj.filename)
                ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                unique_fn = f"hotel{new_hotel.id}_gallery_{ts}_{i+1}{os.path.splitext(original_fn)[1]}"
                try:
                    file_obj.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_fn))
                    db.session.add(HotelImage(hotel_id=new_hotel.id, filename=unique_fn))
                    image_filenames_saved.append(unique_fn)
                except Exception as e:
                    app.logger.error(f"Gallery image save error {original_fn}: {e}")
        
        if main_image_url:
            new_hotel.image_url = main_image_url
        elif image_filenames_saved:
            new_hotel.image_url = url_for('static', filename=f'uploads/hotel_images/{image_filenames_saved[0]}', _external=False)
        else:
            new_hotel.image_url = 'https://via.placeholder.com/800x500.png?text=Main+Hotel+Image'

        db.session.commit()
        log_audit(f"Owner '{current_user.username}' registered new hotel '{name}'.")
        flash('Hotel registration submitted! Now, please add room types.', 'success')
        return redirect(url_for('manage_rooms', hotel_id=new_hotel.id))
        
    return render_template('register_hotel.html', form_data={}, all_amenities=all_amenities)

@app.route('/owner_dashboard')
@role_required('owner')
def owner_dashboard():
    hotels = Hotel.query.filter_by(owner_id=current_user.id).all()
    hotel_ids = [h.id for h in hotels]
    analytics = {'total_revenue': 0, 'occupancy_rate': 0, 'total_bookings': 0, 'popular_room_types': [], 'chart_labels': [], 'chart_data': []}
    if hotel_ids:
        all_bookings = Booking.query.filter(Booking.hotel_id.in_(hotel_ids)).all()
        analytics['total_bookings'] = len(all_bookings)
        analytics['total_revenue'] = sum(b.total_price for b in all_bookings)
        if all_bookings:
            room_type_counts = Counter(b.room_type.name for b in all_bookings)
            analytics['popular_room_types'] = room_type_counts.most_common(3)
        today = date.today(); start_period = today - timedelta(days=30)
        total_possible_room_nights = 0; total_booked_room_nights = 0
        owner_room_types = RoomType.query.filter(RoomType.hotel_id.in_(hotel_ids)).all()
        for i in range(30):
            for room_type in owner_room_types: total_possible_room_nights += get_daily_details(room_type, start_period + timedelta(days=i))['available']
        for booking in Booking.query.filter(Booking.hotel_id.in_(hotel_ids), Booking.check_in_date < today, Booking.check_out_date > start_period).all():
            overlap_start = max(booking.check_in_date, start_period); overlap_end = min(booking.check_out_date, today)
            if (overlap_end - overlap_start).days > 0: total_booked_room_nights += (overlap_end - overlap_start).days
        if total_possible_room_nights > 0: analytics['occupancy_rate'] = (total_booked_room_nights / total_possible_room_nights) * 100
        bookings_by_day = {(start_period + timedelta(days=i)).strftime("%b %d"): 0 for i in range(30)}
        for booking in [b for b in all_bookings if b.created_at.date() >= start_period]:
            day_str = booking.created_at.strftime("%b %d")
            if day_str in bookings_by_day: bookings_by_day[day_str] += 1
        analytics['chart_labels'] = list(bookings_by_day.keys()); analytics['chart_data'] = list(bookings_by_day.values())
    return render_template('owner_dashboard.html', hotels=hotels, analytics=analytics)

@app.route('/hotel/<int:hotel_id>/manage_rooms', methods=['GET', 'POST'])
@role_required(['owner', 'admin'])
@verify_hotel_owner
def manage_rooms(hotel):
    if request.method == 'POST':
        name=request.form.get('name'); price_str=request.form.get('price_per_night'); num_rooms_str=request.form.get('number_of_rooms')
        if not all([name, price_str, num_rooms_str]):
            flash("All fields are required.", "danger"); return redirect(url_for('manage_rooms', hotel_id=hotel.id))
        try: price=float(price_str); num_rooms=int(num_rooms_str); assert price > 0 and num_rooms >= 0
        except: flash("Invalid price or number of rooms.", "danger"); return redirect(url_for('manage_rooms', hotel_id=hotel.id))
        new_room = RoomType(hotel_id=hotel.id, name=name, price_per_night=price, number_of_rooms=num_rooms, description=request.form.get('description'), amenities=request.form.get('amenities'))
        db.session.add(new_room); hotel.update_min_price(); db.session.commit()
        log_audit(f"User '{current_user.username}' added room '{name}' to hotel '{hotel.name}'.")
        flash(f"Room type '{name}' added.", "success"); return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    room_types = hotel.room_types.order_by(RoomType.name.asc()).all()
    return render_template('manage_rooms.html', hotel=hotel, room_types=room_types)

@app.route('/hotel/<int:hotel_id>/edit_room/<int:room_type_id>', methods=['GET', 'POST'])
@role_required(['owner', 'admin'])
@verify_hotel_owner
def edit_room(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    if request.method == 'POST':
        room.name = request.form.get('name'); room.price_per_night = float(request.form.get('price_per_night')); room.number_of_rooms = int(request.form.get('number_of_rooms'))
        room.description = request.form.get('description'); room.amenities = request.form.get('amenities')
        hotel.update_min_price(); db.session.commit()
        log_audit(f"User '{current_user.username}' edited room '{room.name}' in hotel '{hotel.name}'.")
        flash(f"Room '{room.name}' updated.", "success"); return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    return render_template('edit_room.html', hotel=hotel, room=room)

@app.route('/hotel/<int:hotel_id>/delete_room/<int:room_type_id>', methods=['POST'])
@role_required(['owner', 'admin'])
@verify_hotel_owner
def delete_room(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    if room.bookings.first():
        flash(f"Cannot delete '{room.name}' as it has existing bookings.", "danger")
        return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    log_audit(f"User '{current_user.username}' deleted room '{room.name}' from hotel '{hotel.name}'.")
    db.session.delete(room); hotel.update_min_price(); db.session.commit()
    flash(f"Room '{room.name}' deleted.", "success"); return redirect(url_for('manage_rooms', hotel_id=hotel.id))

@app.route('/hotel/<int:hotel_id>/room/<int:room_type_id>/availability')
@role_required(['owner', 'admin'])
@verify_hotel_owner
def manage_availability(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    return render_template('manage_availability.html', hotel=hotel, room=room)

# --- Admin Routes ---
@app.route('/admin_dashboard')
@role_required('admin')
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/admin/reporting')
@role_required('admin')
def admin_reporting():
    total_revenue = db.session.query(func.sum(Booking.total_price)).scalar() or 0
    total_bookings = Booking.query.count()
    total_users = User.query.count()
    total_hotels = Hotel.query.count()
    popular_destinations = db.session.query(Hotel.location, func.count(Booking.id).label('booking_count')).join(Hotel).group_by(Hotel.location).order_by(desc('booking_count')).limit(5).all()
    today = date.today()
    user_growth_labels = []
    user_growth_data = []
    for i in range(29, -1, -1):
        day = today - timedelta(days=i)
        user_growth_labels.append(day.strftime('%b %d'))
        count = User.query.filter(func.date(User.created_at) <= day).count()
        user_growth_data.append(count)
    return render_template('admin_reporting.html', total_revenue=total_revenue, total_bookings=total_bookings, total_users=total_users, total_hotels=total_hotels, popular_destinations=popular_destinations, user_growth_labels=user_growth_labels, user_growth_data=user_growth_data)

@app.route('/admin/audit_log')
@role_required('admin')
def admin_audit_log():
    page = request.args.get('page', 1, type=int)
    logs = AuditLog.query.order_by(desc(AuditLog.timestamp)).paginate(page=page, per_page=30)
    return render_template('admin_audit_log.html', logs=logs)

@app.route('/admin/amenities', methods=['GET', 'POST'])
@role_required('admin')
def manage_amenities():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name and not Amenity.query.filter(func.lower(Amenity.name) == func.lower(name)).first():
            new_amenity = Amenity(name=name)
            db.session.add(new_amenity); db.session.commit()
            log_audit(f"Admin created amenity: '{name}'")
            flash(f"Amenity '{name}' added.", "success")
        else: flash("Amenity already exists or name is invalid.", "danger")
        return redirect(url_for('manage_amenities'))
    amenities = Amenity.query.order_by(Amenity.name).all()
    return render_template('admin_manage_amenities.html', amenities=amenities)

@app.route('/admin/amenity/delete/<int:amenity_id>', methods=['POST'])
@role_required('admin')
def delete_amenity(amenity_id):
    amenity = Amenity.query.get_or_404(amenity_id)
    log_audit(f"Admin deleted amenity: '{amenity.name}'")
    db.session.delete(amenity); db.session.commit()
    flash(f"Amenity '{amenity.name}' deleted.", "warning")
    return redirect(url_for('manage_amenities'))

@app.route('/admin/users')
@role_required('admin')
def manage_users():
    users = User.query.order_by(User.id).all()
    return render_template('admin_manage_users.html', users=users)

@app.route('/admin/user/edit/<int:user_id>', methods=['GET', 'POST'])
@role_required('admin')
def edit_user(user_id):
    user_to_edit = User.query.get_or_404(user_id)
    if user_to_edit.id == current_user.id:
        flash("You cannot edit your own account from this panel.", "warning")
        return redirect(url_for('manage_users'))
    if request.method == 'POST':
        old_role = user_to_edit.role; new_role = request.form.get('role')
        user_to_edit.username = request.form.get('username'); user_to_edit.email = request.form.get('email')
        if new_role in ['user', 'owner', 'admin']: user_to_edit.role = new_role
        new_password = request.form.get('new_password')
        if new_password: user_to_edit.set_password(new_password); flash(f"User '{user_to_edit.username}'s password has been updated.", "info")
        log_audit(f"Admin edited user '{user_to_edit.username}' (ID: {user_id}). Role changed from {old_role} to {new_role}.")
        db.session.commit()
        flash(f"User '{user_to_edit.username}' updated successfully.", 'success')
        return redirect(url_for('manage_users'))
    return render_template('admin_edit_user.html', user=user_to_edit)

@app.route('/admin/user/toggle_active/<int:user_id>', methods=['POST'])
@role_required('admin')
def toggle_user_active(user_id):
    user_to_toggle = User.query.get_or_404(user_id)
    if user_to_toggle.id == current_user.id:
        flash("You cannot change your own active status.", "danger")
        return redirect(url_for('manage_users'))
    user_to_toggle.is_active = not user_to_toggle.is_active
    status = "activated" if user_to_toggle.is_active else "suspended"
    log_audit(f"Admin {status} user '{user_to_toggle.username}' (ID: {user_id}).")
    db.session.commit()
    flash(f"User '{user_to_toggle.username}' has been {status}.", 'success')
    return redirect(url_for('manage_users'))

@app.route('/admin/user/delete/<int:user_id>', methods=['POST'])
@role_required('admin')
def delete_user(user_id):
    user_to_delete = User.query.get_or_404(user_id)
    if user_to_delete.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for('manage_users'))
    log_audit(f"Admin permanently deleted user '{user_to_delete.username}' (ID: {user_id}) and all their data.")
    db.session.delete(user_to_delete)
    db.session.commit()
    flash(f"User '{user_to_delete.username}' has been permanently deleted.", 'warning')
    return redirect(url_for('manage_users'))

@app.route('/admin/hotels')
@role_required('admin')
def manage_hotels():
    hotels = Hotel.query.order_by(Hotel.is_approved.asc(), Hotel.name.asc()).all()
    return render_template('admin_manage_hotels.html', hotels=hotels)

@app.route('/admin/hotel/edit/<int:hotel_id>', methods=['GET', 'POST'])
@role_required('admin')
def edit_hotel_by_admin(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    all_amenities = Amenity.query.order_by(Amenity.name).all()
    if request.method == 'POST':
        hotel.name = request.form.get('name'); hotel.location = request.form.get('location'); hotel.description = request.form.get('description')
        hotel.star_rating = int(request.form.get('star_rating')) if request.form.get('star_rating') else None
        hotel.is_approved = 'is_approved' in request.form
        selected_amenity_ids = request.form.getlist('amenities')
        hotel.amenities = Amenity.query.filter(Amenity.id.in_(selected_amenity_ids)).all()
        log_audit(f"Admin edited hotel '{hotel.name}' (ID: {hotel_id}).")
        db.session.commit()
        flash(f"Hotel '{hotel.name}' has been updated successfully.", "success")
        return redirect(url_for('manage_hotels'))
    return render_template('admin_edit_hotel.html', hotel=hotel, all_amenities=all_amenities)

@app.route('/admin/hotel/delete/<int:hotel_id>', methods=['POST'])
@role_required('admin')
def delete_hotel_by_admin(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    log_audit(f"Admin permanently deleted hotel '{hotel.name}' (ID: {hotel_id}) and all its data.")
    db.session.delete(hotel)
    db.session.commit()
    flash(f"Hotel '{hotel.name}' and all its data have been permanently deleted.", 'warning')
    return redirect(url_for('manage_hotels'))

# --- API Endpoints ---
@app.route('/api/room/<int:room_type_id>/availability')
@login_required
def get_availability_data(room_type_id):
    room_type = RoomType.query.get_or_404(room_type_id)
    if current_user.role != 'admin' and room_type.hotel.owner_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    start_str = request.args.get('start'); end_str = request.args.get('end')
    try:
        start_date = datetime.fromisoformat(start_str.split('T')[0]).date()
        end_date = datetime.fromisoformat(end_str.split('T')[0]).date()
    except: return jsonify({'error': 'Invalid date format'}), 400
    overrides = {o.date: o for o in Availability.query.filter(Availability.room_type_id == room_type_id, Availability.date.between(start_date, end_date)).all()}
    bookings_in_range = Booking.query.filter(Booking.room_type_id == room_type_id, Booking.check_in_date < end_date, Booking.check_out_date > start_date).all()
    events = []
    current_date = start_date
    while current_date < end_date:
        details = get_daily_details(room_type, current_date)
        booked_count = sum(1 for b in bookings_in_range if b.check_in_date <= current_date < b.check_out_date)
        remaining = details['available'] - booked_count
        events.append({'start': current_date.isoformat(), 'title': f"Avail: {remaining} | ${details['price']:.2f}",
                       'allDay': True, 'color': '#28a745' if remaining > 0 else '#dc3545',
                       'borderColor': '#007bff' if current_date in overrides else 'transparent'})
        current_date += timedelta(days=1)
    return jsonify(events)

@app.route('/api/room/<int:room_type_id>/availability/update', methods=['POST'])
@login_required
def update_availability(room_type_id):
    room_type = RoomType.query.get_or_404(room_type_id)
    if current_user.role != 'admin' and room_type.hotel.owner_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.json
    try:
        target_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        price = float(data['price']) if data.get('price') and data['price'].strip() else None
        available_rooms = int(data['available_rooms']) if data.get('available_rooms') and data['available_rooms'].strip() else None
    except (ValueError, TypeError, KeyError): return jsonify({'error': 'Invalid data'}), 400
    override = Availability.query.filter_by(room_type_id=room_type_id, date=target_date).first()
    if price is None and available_rooms is None:
        if override: db.session.delete(override)
    else:
        if not override:
            override = Availability(room_type_id=room_type_id, date=target_date)
            db.session.add(override)
        override.price = price
        override.available_rooms = available_rooms
    db.session.commit()
    log_audit(f"User '{current_user.username}' updated availability for room '{room_type.name}' on {target_date}.")
    return jsonify({'success': True})

@app.route('/api/room/<int:room_type_id>/calculate_price')
def api_calculate_price(room_type_id):
    check_in_str=request.args.get('check_in'); check_out_str=request.args.get('check_out')
    try:
        check_in = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        check_out = datetime.strptime(check_out_str, '%Y-%m-%d').date()
        if check_out <= check_in: raise ValueError()
    except: return jsonify({'error': 'Invalid dates'}), 400
    result = check_availability_and_calculate_price(room_type_id, check_in, check_out)
    return jsonify(result)

# --- Wishlist Routes ---
@app.route('/wishlist/add/<int:hotel_id>', methods=['POST'])
@login_required
def add_to_wishlist(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    if not Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel.id).first():
        db.session.add(Wishlist(user_id=current_user.id, hotel_id=hotel.id)); db.session.commit()
        flash(f'{hotel.name} added to your wishlist!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/wishlist/remove/<int:hotel_id>', methods=['POST'])
@login_required
def remove_from_wishlist(hotel_id):
    item = Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel.id).first()
    if item: db.session.delete(item); db.session.commit(); flash('Hotel removed from your wishlist.', 'success')
    return redirect(request.referrer or url_for('my_wishlist'))

@app.route('/my_wishlist')
@login_required
def my_wishlist():
    wishlisted_hotels = Hotel.query.join(Wishlist).filter(Wishlist.user_id == current_user.id).all()
    return render_template('my_wishlist.html', hotels=wishlisted_hotels)

# --- App Runner ---
def create_initial_data():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin', email='admin@hotelapp.com', role='admin', is_active=True)
            admin_user.set_password('StrongAdminP@ssw0rd!')
            db.session.add(admin_user)
            print("Admin user created.")
        if Amenity.query.count() == 0:
            amenities = ['Wifi', 'Pool', 'Gym', 'Free Parking', 'Pet Friendly', 'Restaurant', 'Room Service', 'Air Conditioning', 'Spa', 'Bar']
            for name in amenities: db.session.add(Amenity(name=name))
            print(f"Added {len(amenities)} default amenities.")
        db.session.commit()

if __name__ == '__main__':
    create_initial_data()
    app.logger.info('HotelApp application started successfully.')
    app.run(debug=True, port=5001)