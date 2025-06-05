
from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date
from functools import wraps
import os
import traceback
from io import BytesIO
from weasyprint import HTML
from sqlalchemy import or_, and_, select # Added select for subquery

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
    # Relationship for wishlist items initiated by this user
    wishlist_items = db.relationship('Wishlist', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class HotelImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

class Hotel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(255), nullable=True, default='https://via.placeholder.com/800x500.png?text=Main+Hotel+Image')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    star_rating = db.Column(db.Integer, nullable=True) 
    amenities = db.Column(db.String(300), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    gallery_images = db.relationship('HotelImage', backref='hotel_ref', lazy='dynamic', cascade="all, delete-orphan")
    bookings = db.relationship('Booking', backref='hotel_booking_ref', lazy=True, foreign_keys='Booking.hotel_id', cascade="all, delete-orphan")
    # Relationship for users who have wishlisted this hotel
    wishlisted_by_users = db.relationship('Wishlist', backref='hotel', lazy='dynamic', cascade='all, delete-orphan')

    def get_amenities_list(self):
        return [a.strip() for a in self.amenities.split(',') if a.strip()] if self.amenities else []
    def to_dict_for_map(self):
        return {'name': self.name, 'lat': self.latitude, 'lng': self.longitude, 'url': url_for('hotel_detail', hotel_id=self.id),
                'imageUrl': self.image_url, 'price': "{:.2f}".format(self.price_per_night), 'location': self.location}

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    check_out_date = db.Column(db.Date, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Confirmed')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class Wishlist(db.Model): # New Model
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id', ondelete='CASCADE'), nullable=False)
    added_on = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'hotel_id', name='_user_hotel_wishlist_uc'),)

# --- Helper Functions ---
def role_required(role_name):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated: return login_manager.unauthorized()
            if current_user.role != role_name:
                flash(f"Access Denied: '{role_name}' privileges required.", "danger"); return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.context_processor # Makes function available in all templates
def utility_processor():
    def is_hotel_in_wishlist(hotel_id):
        if current_user.is_authenticated:
            return Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel_id).first() is not None
        return False
    return dict(is_hotel_in_wishlist=is_hotel_in_wishlist)

# --- Routes ---
# (index, register, login, logout, hotel_detail, booking_confirmed, download_receipt, 
#  register_hotel, owner_dashboard, admin_dashboard, approve_hotel, reject_hotel are largely the same as the very last app.py,
#  but I will include them all for completeness)

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
        except ValueError: pass
    if max_price_str:
        try: query = query.filter(Hotel.price_per_night <= float(max_price_str))
        except ValueError: pass
    if star_rating_str:
        try:
            sr = int(star_rating_str)
            if 1 <= sr <= 5: query = query.filter(Hotel.star_rating >= sr)
        except ValueError: pass
    if amenities_filter_str:
        req_amenities = [a.strip().lower() for a in amenities_filter_str.split(',') if a.strip()]
        if req_amenities:
            for amenity in req_amenities: query = query.filter(Hotel.amenities.ilike(f"%{amenity}%"))
    filtered_hotels = query.order_by(Hotel.name.asc()).all()
    return render_template('index.html', hotels=filtered_hotels)

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
        # --- Handle Email Update ---
        new_email = request.form.get('email', '').strip()
        if new_email and new_email != user.email:
            existing_user = User.query.filter(and_(User.email == new_email, User.id != user.id)).first()
            if existing_user:
                flash('That email address is already in use. Please choose another.', 'danger')
            else:
                user.email = new_email
                flash('Your email has been updated successfully.', 'success')

        # --- Handle Password Update ---
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if new_password: # User wants to change password
            if not current_password or not confirm_password:
                flash('To change your password, please provide your current password and confirm the new one.', 'warning')
            elif not user.check_password(current_password):
                flash('Your current password is not correct.', 'danger')
            elif new_password != confirm_password:
                flash('The new passwords do not match.', 'danger')
            elif len(new_password) < 8:
                 flash('New password must be at least 8 characters long.', 'danger')
            else:
                user.set_password(new_password)
                flash('Your password has been updated successfully.', 'success')
        
        db.session.commit()
        return redirect(url_for('profile'))

    # --- Handle GET request ---
    # Fetch recent bookings to display on the profile page
    recent_bookings = Booking.query.filter_by(user_id=user.id).order_by(Booking.created_at.desc()).limit(5).all()
    return render_template('profile.html', user=user, bookings=recent_bookings)


@app.route('/hotel/<int:hotel_id>', methods=['GET', 'POST'])
def hotel_detail(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    if not hotel.is_approved and (not current_user.is_authenticated or current_user.role != 'admin'):
        flash("This hotel is not yet approved or does not exist.", "warning"); return redirect(url_for('index'))
    if request.method == 'POST':
        if not current_user.is_authenticated:
            flash("Please log in to book a hotel.", "warning"); return redirect(url_for('login', next=request.url))
        check_in_str = request.form.get('check_in_date')
        check_out_str = request.form.get('check_out_date')
        if not check_in_str or not check_out_str:
            flash("Please select check-in and check-out dates.", "danger"); return redirect(url_for('hotel_detail', hotel_id=hotel_id))
        try:
            check_in_date_obj = datetime.strptime(check_in_str, '%Y-%m-%d').date()
            check_out_date_obj = datetime.strptime(check_out_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format.", "danger"); return redirect(url_for('hotel_detail', hotel_id=hotel_id))
        if check_in_date_obj < date.today():
            flash("Check-in date cannot be in the past.", "danger"); return redirect(url_for('hotel_detail', hotel_id=hotel_id))
        if check_out_date_obj <= check_in_date_obj:
            flash("Check-out date must be after check-in date.", "danger"); return redirect(url_for('hotel_detail', hotel_id=hotel_id))
        num_nights = (check_out_date_obj - check_in_date_obj).days
        total_price = num_nights * hotel.price_per_night
        booking = Booking(user_id=current_user.id, hotel_id=hotel.id, check_in_date=check_in_date_obj, check_out_date=check_out_date_obj, total_price=total_price)
        db.session.add(booking); db.session.commit() 
        flash(f'Successfully booked {hotel.name}! Your booking is confirmed.', 'success')
        return redirect(url_for('booking_confirmed', booking_id=booking.id))

    all_images_for_carousel = []
    gallery_items = hotel.gallery_images.order_by(HotelImage.id).all()
    gallery_filenames_in_db = [img.filename for img in gallery_items]
    main_image_added_to_carousel = False
    if hotel.image_url and not hotel.image_url.startswith('https://via.placeholder.com'):
        all_images_for_carousel.append({'url': hotel.image_url, 'alt': f"{hotel.name} - Main View"})
        main_image_added_to_carousel = True
        main_img_filename_component = hotel.image_url.split('/')[-1]
        if main_img_filename_component in gallery_filenames_in_db:
             gallery_filenames_in_db.remove(main_img_filename_component) 
    for image_db_entry_filename in gallery_filenames_in_db:
        all_images_for_carousel.append({'url': url_for('static', filename=f'uploads/hotel_images/{image_db_entry_filename}'), 'alt': f"{hotel.name} - View"})
    if not all_images_for_carousel:
        all_images_for_carousel.append({'url': 'https://via.placeholder.com/800x500.png?text=No+Images+Available', 'alt': 'No Images Available'})
    return render_template('hotel_detail.html', hotel=hotel, today=date.today().isoformat(), carousel_images=all_images_for_carousel)

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
    try:
        html_string = render_template('receipt_template.html', booking=booking, hotel=booking.hotel, user=booking.booker, 
                                      num_nights=(booking.check_out_date - booking.check_in_date).days, booking_date=booking.created_at)
        pdf_bytes = HTML(string=html_string).write_pdf()
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=receipt_booking_{booking.id}.pdf'
        return response
    except Exception as e:
        app.logger.error(f"PDF Error (Booking {booking.id}): {e}\n{traceback.format_exc()}")
        flash("Error generating PDF. Contact support.", "danger")
        return redirect(request.referrer or url_for('my_bookings'))

@app.route('/my_bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/register_hotel', methods=['GET', 'POST'])
@role_required('owner')
def register_hotel():
    form_data = request.form if request.method == 'POST' else {}
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        description = request.form.get('description')
        price_per_night_str = request.form.get('price_per_night')
        main_image_url = request.form.get('main_image_url', '').strip() 
        star_rating_str = request.form.get('star_rating')
        amenities = request.form.get('amenities', '').strip()
        latitude_str = request.form.get('latitude')
        longitude_str = request.form.get('longitude')
        errors = {}
        if not name: errors['name'] = "Name is required."
        if not location: errors['location'] = "Location is required."
        price_float = 0.0
        if price_per_night_str:
            try:
                price_float = float(price_per_night_str)
                if price_float <= 0: errors['price_per_night'] = "Price must be positive."
            except ValueError: errors['price_per_night'] = "Invalid price format."
        else: errors['price_per_night'] = "Price is required."
        star_rating = None
        if star_rating_str and star_rating_str.strip():
            try:
                star_rating_val = int(star_rating_str)
                if 1 <= star_rating_val <= 5: star_rating = star_rating_val
                else: flash("Star rating (1-5) invalid, ignored.", "warning")
            except ValueError: flash("Star rating format invalid, ignored.", "warning")
        latitude = None
        if latitude_str and latitude_str.strip():
            try: latitude = float(latitude_str)
            except ValueError: flash("Latitude format invalid, ignored.", "warning")
        longitude = None
        if longitude_str and longitude_str.strip():
            try: longitude = float(longitude_str)
            except ValueError: flash("Longitude format invalid, ignored.", "warning")
        if errors:
            for msg in errors.values(): flash(msg, 'danger')
            current_form_data = {key: request.form[key] for key in request.form}
            return render_template('register_hotel.html', form_data=current_form_data)
        effective_main_image_url = main_image_url if main_image_url else 'https://via.placeholder.com/800x500.png?text=Main+Hotel+Image'
        new_hotel = Hotel(name=name, location=location, description=description, price_per_night=price_float,
                          star_rating=star_rating, amenities=amenities, latitude=latitude, longitude=longitude,
                          image_url=effective_main_image_url, owner_id=current_user.id, is_approved=False)
        db.session.add(new_hotel); db.session.flush()
        uploaded_files = request.files.getlist('gallery_images_upload')
        image_filenames_saved = []
        for i, file_obj in enumerate(uploaded_files):
            if file_obj.filename == '': continue
            if i >= 5: flash("Max 5 gallery images. Extras ignored.", "warning"); break
            if allowed_file(file_obj.filename):
                original_fn = secure_filename(file_obj.filename)
                ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
                fn_base, fn_ext = os.path.splitext(original_fn)
                unique_fn = f"hotel{new_hotel.id}_gallery_{ts}_{i+1}{fn_ext}"
                try:
                    file_obj.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_fn))
                    db.session.add(HotelImage(hotel_id=new_hotel.id, filename=unique_fn))
                    image_filenames_saved.append(unique_fn)
                except Exception as e:
                    app.logger.error(f"Gallery image save error {original_fn}: {e}")
                    flash(f"Error uploading {original_fn}.", "danger")
            elif file_obj.filename != '': flash(f"Gallery img '{file_obj.filename}' invalid type.", "warning")
        if new_hotel.image_url.startswith('https://via.placeholder.com') and image_filenames_saved:
            new_hotel.image_url = url_for('static', filename=f'uploads/hotel_images/{image_filenames_saved[0]}', _external=False)
        db.session.commit()
        flash('Hotel registration submitted for approval!', 'success')
        return redirect(url_for('owner_dashboard'))
    return render_template('register_hotel.html', form_data={})

@app.route('/owner_dashboard')
@role_required('owner')
def owner_dashboard():
    hotels_owned = Hotel.query.filter_by(owner_id=current_user.id).order_by(Hotel.name).all()
    hotel_ids = [h.id for h in hotels_owned]
    bookings_for_my_hotels = []
    if hotel_ids:
        bookings_for_my_hotels = Booking.query.filter(Booking.hotel_id.in_(hotel_ids)).order_by(Booking.created_at.desc()).all()
    return render_template('owner_dashboard.html', hotels=hotels_owned, bookings=bookings_for_my_hotels)

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
    for img in hotel.gallery_images.all():
        try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], img.filename))
        except OSError as e: app.logger.error(f"Error deleting file {img.filename}: {e}")
    Booking.query.filter_by(hotel_id=hotel.id).delete() # Also deletes bookings for this hotel
    db.session.delete(hotel); db.session.commit()
    flash(f'Hotel "{hotel.name}", its images, and bookings removed.', 'warning')
    return redirect(url_for('admin_dashboard'))

# --- Wishlist Routes ---
@app.route('/wishlist/add/<int:hotel_id>', methods=['POST'])
@login_required
def add_to_wishlist(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    if current_user.role != 'user': # Only regular users can have wishlists
        flash("Only users can add to wishlist.", "warning")
        return redirect(request.referrer or url_for('index'))
        
    existing_item = Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel.id).first()
    if existing_item:
        flash(f'{hotel.name} is already in your wishlist.', 'info')
    else:
        wishlist_item = Wishlist(user_id=current_user.id, hotel_id=hotel.id)
        db.session.add(wishlist_item)
        db.session.commit()
        flash(f'{hotel.name} added to your wishlist!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/wishlist/remove/<int:hotel_id>', methods=['POST'])
@login_required
def remove_from_wishlist(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    wishlist_item = Wishlist.query.filter_by(user_id=current_user.id, hotel_id=hotel.id).first()
    if wishlist_item:
        db.session.delete(wishlist_item)
        db.session.commit()
        flash(f'{hotel.name} removed from your wishlist.', 'success')
    else:
        flash(f'{hotel.name} was not found in your wishlist.', 'warning')
    return redirect(request.referrer or url_for('my_wishlist'))

@app.route('/my_wishlist')
@login_required
def my_wishlist():
    if current_user.role != 'user':
        flash("This page is for users.", "info")
        return redirect(url_for('index'))

    # Using a subquery to get hotel_ids from the user's wishlist
    wishlisted_hotel_ids_stmt = select(Wishlist.hotel_id).filter_by(user_id=current_user.id)
    # Fetch the actual Hotel objects based on these IDs
    wishlisted_hotels = Hotel.query.filter(Hotel.id.in_(wishlisted_hotel_ids_stmt)).order_by(Hotel.name).all()
    
    return render_template('my_wishlist.html', hotels=wishlisted_hotels)
# --- End Wishlist Routes ---

def create_admin_user_if_not_exists():
    with app.app_context():
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin', email='admin@hotelapp.com', role='admin')
            admin_user.set_password('StrongAdminP@ssw0rd!')
            db.session.add(admin_user); db.session.commit()
            print("Admin user created: admin / StrongAdminP@ssw0rd!")
        else: print("Admin user 'admin' already exists.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    create_admin_user_if_not_exists()
    app.run(debug=True, port=5001)