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
from sqlalchemy import or_, and_, select, func
from collections import Counter

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_super_duper_secret_key_for_wishlist_v6' # CHANGE THIS!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hotel_booking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

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
    return User.query.get(int(user_id))

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')
    hotels_owned = db.relationship('Hotel', backref='owner_user', lazy=True, foreign_keys='Hotel.owner_id')
    bookings = db.relationship('Booking', backref='booker', lazy=True, foreign_keys='Booking.user_id')
    wishlist_items = db.relationship('Wishlist', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class HotelImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

class RoomImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

class Availability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    room_type_id = db.Column(db.Integer, db.ForeignKey('room_type.id', ondelete='CASCADE'), nullable=False)
    price = db.Column(db.Float, nullable=True)
    available_rooms = db.Column(db.Integer, nullable=True)
    __table_args__ = (db.UniqueConstraint('date', 'room_type_id', name='_date_room_uc'),)

class RoomType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=False) # Default price
    number_of_rooms = db.Column(db.Integer, nullable=False, default=1) # Default availability
    amenities = db.Column(db.String(300), nullable=True)
    main_image_url = db.Column(db.String(255), nullable=True, default='https://via.placeholder.com/800x500.png?text=Room+Image')
    gallery_images = db.relationship('RoomImage', backref='room_type_ref', lazy='dynamic', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='room_type', lazy='dynamic')
    availabilities = db.relationship('Availability', backref='room_type', lazy='dynamic', cascade="all, delete-orphan")

    def get_amenities_list(self):
        return [a.strip() for a in self.amenities.split(',') if a.strip()] if self.amenities else []

class Hotel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=True)
    image_url = db.Column(db.String(255), nullable=True, default='https://via.placeholder.com/800x500.png?text=Main+Hotel+Image')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    star_rating = db.Column(db.Integer, nullable=True) 
    amenities = db.Column(db.String(300), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    gallery_images = db.relationship('HotelImage', backref='hotel_ref', lazy='dynamic', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='hotel', lazy=True, foreign_keys='Booking.hotel_id', cascade="all, delete-orphan")
    wishlisted_by_users = db.relationship('Wishlist', backref='hotel', lazy='dynamic', cascade='all, delete-orphan')
    room_types = db.relationship('RoomType', backref='hotel', lazy='dynamic', cascade="all, delete-orphan")

    def get_amenities_list(self):
        return [a.strip() for a in self.amenities.split(',') if a.strip()] if self.amenities else []
    
    def get_min_price(self):
        return db.session.query(func.min(RoomType.price_per_night)).filter(RoomType.hotel_id == self.id).scalar()

    def update_min_price(self):
        min_price = self.get_min_price()
        self.price_per_night = min_price
        db.session.add(self)

    def to_dict_for_map(self):
        min_price = self.get_min_price()
        return {
            'name': self.name, 'lat': self.latitude, 'lng': self.longitude, 
            'url': url_for('hotel_detail', hotel_id=self.id), 'imageUrl': self.image_url, 
            'price': "{:.2f}".format(min_price) if min_price else "N/A", 'location': self.location
        }

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
def role_required(role_name):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role != role_name:
                flash(f"Access Denied: '{role_name}' privileges required.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def verify_hotel_owner(f):
    @wraps(f)
    @role_required('owner')
    def decorated_function(hotel_id, *args, **kwargs):
        hotel = Hotel.query.get_or_404(hotel_id)
        if hotel.owner_id != current_user.id:
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
            Booking.room_type_id == room_type_id,
            Booking.check_in_date <= current_date,
            Booking.check_out_date > current_date
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
    min_price_str = request.args.get('min_price', '').strip()
    max_price_str = request.args.get('max_price', '').strip()
    star_rating_str = request.args.get('star_rating', '').strip()
    amenities_filter_str = request.args.get('amenities_filter', '').strip()
    if search_term: query = query.filter(or_(Hotel.name.ilike(f"%{search_term}%"), Hotel.location.ilike(f"%{search_term}%")))
    if min_price_str:
        try: query = query.filter(Hotel.price_per_night >= float(min_price_str))
        except (ValueError, TypeError): pass
    if max_price_str:
        try: query = query.filter(Hotel.price_per_night <= float(max_price_str))
        except (ValueError, TypeError): pass
    if star_rating_str:
        try:
            sr = int(star_rating_str)
            if 1 <= sr <= 5: query = query.filter(Hotel.star_rating >= sr)
        except (ValueError, TypeError): pass
    if amenities_filter_str:
        req_amenities = [a.strip().lower() for a in amenities_filter_str.split(',') if a.strip()]
        if req_amenities:
            for amenity in req_amenities: query = query.filter(Hotel.amenities.ilike(f"%{amenity}%"))
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
        new_user = User(username=username, email=email, role=role)
        new_user.set_password(password)
        db.session.add(new_user); db.session.commit()
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
            login_user(user, remember=request.form.get('remember_me'))
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            if user.role == 'admin': return redirect(next_page or url_for('admin_dashboard'))
            elif user.role == 'owner': return redirect(next_page or url_for('owner_dashboard'))
            return redirect(next_page or url_for('index'))
        else: flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = User.query.get_or_404(current_user.id)
    if request.method == 'POST':
        new_email = request.form.get('email', '').strip()
        if new_email and new_email != user.email:
            existing_user = User.query.filter(and_(User.email == new_email, User.id != user.id)).first()
            if existing_user: flash('That email address is already in use.', 'danger')
            else: user.email = new_email; flash('Your email has been updated.', 'success')
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if new_password:
            if not user.check_password(current_password): flash('Your current password is not correct.', 'danger')
            elif new_password != confirm_password: flash('The new passwords do not match.', 'danger')
            else: user.set_password(new_password); flash('Your password has been updated.', 'success')
        db.session.commit()
        return redirect(url_for('profile'))
    recent_bookings = Booking.query.filter_by(user_id=user.id).order_by(Booking.created_at.desc()).limit(5).all()
    return render_template('profile.html', user=user, bookings=recent_bookings)

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
    db.session.add(booking)
    db.session.commit()
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
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)

# --- Owner Routes ---
@app.route('/register_hotel', methods=['GET', 'POST'])
@role_required('owner')
def register_hotel():
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        if not name or not location:
            flash("Hotel Name and Location are required.", "danger")
            return render_template('register_hotel.html', form_data=request.form)
        new_hotel = Hotel(
            name=name, location=location, owner_id=current_user.id, is_approved=False,
            description=request.form.get('description'),
            star_rating=int(request.form.get('star_rating')) if request.form.get('star_rating') else None,
            amenities=request.form.get('amenities', '').strip(),
            latitude=float(request.form.get('latitude')) if request.form.get('latitude') else None,
            longitude=float(request.form.get('longitude')) if request.form.get('longitude') else None
        )
        db.session.add(new_hotel); db.session.commit()
        flash('Hotel registration submitted! Now, please add room types to your new hotel.', 'success')
        return redirect(url_for('manage_rooms', hotel_id=new_hotel.id))
    return render_template('register_hotel.html', form_data={})

@app.route('/owner_dashboard')
@role_required('owner')
def owner_dashboard():
    hotels = Hotel.query.filter_by(owner_id=current_user.id).all()
    hotel_ids = [h.id for h in hotels]
    
    analytics = {
        'total_revenue': 0,
        'occupancy_rate': 0,
        'total_bookings': 0,
        'popular_room_types': [],
        'chart_labels': [],
        'chart_data': []
    }

    if hotel_ids:
        # --- Basic Stats ---
        all_bookings = Booking.query.filter(Booking.hotel_id.in_(hotel_ids)).all()
        analytics['total_bookings'] = len(all_bookings)
        analytics['total_revenue'] = sum(b.total_price for b in all_bookings)

        # --- Popular Room Types ---
        if all_bookings:
            room_type_counts = Counter(b.room_type.name for b in all_bookings)
            analytics['popular_room_types'] = room_type_counts.most_common(3)

        # --- Occupancy Rate (Last 30 Days) ---
        today = date.today()
        start_period = today - timedelta(days=30)
        total_possible_room_nights = 0
        total_booked_room_nights = 0
        
        owner_room_types = RoomType.query.filter(RoomType.hotel_id.in_(hotel_ids)).all()

        for i in range(30):
            current_day = start_period + timedelta(days=i)
            for room_type in owner_room_types:
                daily_details = get_daily_details(room_type, current_day)
                total_possible_room_nights += daily_details['available']
        
        bookings_in_period = Booking.query.filter(
            Booking.hotel_id.in_(hotel_ids),
            Booking.check_in_date < today,
            Booking.check_out_date > start_period
        ).all()
        
        for booking in bookings_in_period:
            overlap_start = max(booking.check_in_date, start_period)
            overlap_end = min(booking.check_out_date, today)
            booked_nights_in_period = (overlap_end - overlap_start).days
            if booked_nights_in_period > 0:
                total_booked_room_nights += booked_nights_in_period

        if total_possible_room_nights > 0:
            analytics['occupancy_rate'] = (total_booked_room_nights / total_possible_room_nights) * 100

        # --- Booking Trends Chart (Last 30 Days) ---
        bookings_by_day = { (start_period + timedelta(days=i)).strftime("%b %d"): 0 for i in range(30) }
        recent_bookings = [b for b in all_bookings if b.created_at.date() >= start_period]
        
        for booking in recent_bookings:
            day_str = booking.created_at.strftime("%b %d")
            if day_str in bookings_by_day:
                bookings_by_day[day_str] += 1
        
        analytics['chart_labels'] = list(bookings_by_day.keys())
        analytics['chart_data'] = list(bookings_by_day.values())

    return render_template('owner_dashboard.html', hotels=hotels, analytics=analytics)


@app.route('/hotel/<int:hotel_id>/manage_rooms', methods=['GET', 'POST'])
@verify_hotel_owner
def manage_rooms(hotel):
    if request.method == 'POST':
        name = request.form.get('name')
        price_str = request.form.get('price_per_night')
        num_rooms_str = request.form.get('number_of_rooms')
        if not all([name, price_str, num_rooms_str]):
            flash("All fields are required.", "danger")
            return redirect(url_for('manage_rooms', hotel_id=hotel.id))
        try:
            price = float(price_str); num_rooms = int(num_rooms_str)
            if price <= 0 or num_rooms <= 0: raise ValueError
        except (ValueError, TypeError):
            flash("Invalid price or number of rooms.", "danger")
            return redirect(url_for('manage_rooms', hotel_id=hotel.id))
        new_room = RoomType(
            hotel_id=hotel.id, name=name, price_per_night=price, number_of_rooms=num_rooms,
            description=request.form.get('description'), amenities=request.form.get('amenities')
        )
        db.session.add(new_room)
        hotel.update_min_price()
        db.session.commit()
        flash(f"Room type '{name}' added.", "success")
        return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    room_types = hotel.room_types.order_by(RoomType.name.asc()).all()
    return render_template('manage_rooms.html', hotel=hotel, room_types=room_types)

@app.route('/hotel/<int:hotel_id>/edit_room/<int:room_type_id>', methods=['GET', 'POST'])
@verify_hotel_owner
def edit_room(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    if request.method == 'POST':
        room.name = request.form.get('name')
        room.price_per_night = float(request.form.get('price_per_night'))
        room.number_of_rooms = int(request.form.get('number_of_rooms'))
        room.description = request.form.get('description')
        room.amenities = request.form.get('amenities')
        hotel.update_min_price()
        db.session.commit()
        flash(f"Room '{room.name}' updated.", "success")
        return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    return render_template('edit_room.html', hotel=hotel, room=room)

@app.route('/hotel/<int:hotel_id>/delete_room/<int:room_type_id>', methods=['POST'])
@verify_hotel_owner
def delete_room(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    if room.bookings.first():
        flash(f"Cannot delete '{room.name}' as it has existing bookings.", "danger")
        return redirect(url_for('manage_rooms', hotel_id=hotel.id))
    db.session.delete(room)
    hotel.update_min_price()
    db.session.commit()
    flash(f"Room '{room.name}' deleted.", "success")
    return redirect(url_for('manage_rooms', hotel_id=hotel.id))

@app.route('/hotel/<int:hotel_id>/room/<int:room_type_id>/availability')
@verify_hotel_owner
def manage_availability(hotel, room_type_id):
    room = RoomType.query.filter_by(id=room_type_id, hotel_id=hotel.id).first_or_404()
    return render_template('manage_availability.html', hotel=hotel, room=room)

# --- API Endpoints for Calendar ---
@app.route('/api/room/<int:room_type_id>/availability')
@login_required
def get_availability_data(room_type_id):
    room_type = RoomType.query.get_or_404(room_type_id)
    if room_type.hotel.owner_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    start_str = request.args.get('start'); end_str = request.args.get('end')
    try:
        start_date = datetime.fromisoformat(start_str.split('T')[0]).date()
        end_date = datetime.fromisoformat(end_str.split('T')[0]).date()
    except: return jsonify({'error': 'Invalid date format'}), 400
    
    overrides = {o.date: o for o in Availability.query.filter(
        Availability.room_type_id == room_type_id, Availability.date.between(start_date, end_date)
    ).all()}
    
    bookings_in_range = Booking.query.filter(
        Booking.room_type_id == room_type_id,
        Booking.check_in_date < end_date,
        Booking.check_out_date > start_date
    ).all()
    
    events = []
    current_date = start_date
    while current_date < end_date:
        details = get_daily_details(room_type, current_date)
        booked_count = 0
        for booking in bookings_in_range:
            if booking.check_in_date <= current_date < booking.check_out_date:
                booked_count += 1

        remaining = details['available'] - booked_count
        events.append({
            'start': current_date.isoformat(),
            'title': f"Avail: {remaining} | ${details['price']:.2f}",
            'allDay': True,
            'color': '#28a745' if remaining > 0 else '#dc3545',
            'borderColor': '#007bff' if current_date in overrides else 'transparent'
        })
        current_date += timedelta(days=1)
    return jsonify(events)

@app.route('/api/room/<int:room_type_id>/availability/update', methods=['POST'])
@login_required
def update_availability(room_type_id):
    room_type = RoomType.query.get_or_404(room_type_id)
    if room_type.hotel.owner_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.json
    try:
        target_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        price_str = data.get('price'); avail_str = data.get('available_rooms')
        price = float(price_str) if price_str and price_str.strip() else None
        available_rooms = int(avail_str) if avail_str and avail_str.strip() else None
    except (ValueError, TypeError, KeyError): return jsonify({'error': 'Invalid data'}), 400
    
    override = Availability.query.filter_by(room_type_id=room_type_id, date=target_date).first()
    is_default_price = price is None
    is_default_avail = available_rooms is None
    
    if is_default_price and is_default_avail:
        if override: db.session.delete(override)
    else:
        if not override:
            override = Availability(room_type_id=room_type_id, date=target_date)
            db.session.add(override)
        override.price = price
        override.available_rooms = available_rooms
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/room/<int:room_type_id>/calculate_price')
def api_calculate_price(room_type_id):
    check_in_str = request.args.get('check_in'); check_out_str = request.args.get('check_out')
    try:
        check_in = datetime.strptime(check_in_str, '%Y-%m-%d').date()
        check_out = datetime.strptime(check_out_str, '%Y-%m-%d').date()
        if check_out <= check_in: raise ValueError()
    except: return jsonify({'error': 'Invalid dates'}), 400
    result = check_availability_and_calculate_price(room_type_id, check_in, check_out)
    return jsonify(result)

# --- Admin & Wishlist Routes ---
@app.route('/admin_dashboard')
@role_required('admin')
def admin_dashboard():
    all_users = User.query.order_by(User.username).all()
    all_hotels = Hotel.query.order_by(Hotel.is_approved.asc(), Hotel.name.asc()).all()
    all_bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template('admin_dashboard.html', users=all_users, hotels=all_hotels, bookings=all_bookings)

@app.route('/admin/approve_hotel/<int:hotel_id>', methods=['POST'])
@role_required('admin')
def approve_hotel(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    hotel.is_approved = True; db.session.commit()
    flash(f'Hotel "{hotel.name}" approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject_hotel/<int:hotel_id>', methods=['POST'])
@role_required('admin')
def reject_hotel(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    db.session.delete(hotel); db.session.commit()
    flash(f'Hotel "{hotel.name}" and all its data removed.', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/wishlist/add/<int:hotel_id>', methods=['POST'])
@login_required
def add_to_wishlist(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    existing_item = Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel.id).first()
    if not existing_item:
        wishlist_item = Wishlist(user_id=current_user.id, hotel_id=hotel.id)
        db.session.add(wishlist_item); db.session.commit()
        flash(f'{hotel.name} added to your wishlist!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/wishlist/remove/<int:hotel_id>', methods=['POST'])
@login_required
def remove_from_wishlist(hotel_id):
    wishlist_item = Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel_id).first()
    if wishlist_item:
        db.session.delete(wishlist_item); db.session.commit()
        flash('Hotel removed from your wishlist.', 'success')
    return redirect(request.referrer or url_for('my_wishlist'))

@app.route('/my_wishlist')
@login_required
def my_wishlist():
    wishlisted_hotels = Hotel.query.join(Wishlist).filter(Wishlist.user_id == current_user.id).all()
    return render_template('my_wishlist.html', hotels=wishlisted_hotels)

# --- App Runner ---
def create_admin_user_if_not_exists():
    with app.app_context():
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin', email='admin@hotelapp.com', role='admin')
            admin_user.set_password('StrongAdminP@ssw0rd!')
            db.session.add(admin_user); db.session.commit()
            print("Admin user created: admin / StrongAdminP@ssw0rd!")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    create_admin_user_if_not_exists()
    app.run(debug=True, port=5001)