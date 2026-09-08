CREATE TABLE demo.properties (
  property_id BIGINT PRIMARY KEY,
  property_code VARCHAR(12) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  city VARCHAR(80) NOT NULL,
  state VARCHAR(40) NOT NULL,
  country VARCHAR(60) NOT NULL,
  currency VARCHAR(3) NOT NULL
);
CREATE TABLE demo.hotels (
  hotel_id BIGINT PRIMARY KEY,
  property_id BIGINT NOT NULL REFERENCES demo.properties(property_id),
  brand_name VARCHAR(100) NOT NULL,
  star_rating INTEGER NOT NULL CHECK (star_rating >= 1),
  status VARCHAR(20) NOT NULL
);
CREATE TABLE demo.room_types (
  room_type_id BIGINT PRIMARY KEY,
  hotel_id BIGINT NOT NULL REFERENCES demo.hotels(hotel_id),
  name VARCHAR(80) NOT NULL,
  max_occupancy INTEGER NOT NULL CHECK (max_occupancy >= 1),
  base_rate DECIMAL(10,2) NOT NULL CHECK (base_rate >= 0)
);
CREATE TABLE demo.rooms (
  room_id BIGINT PRIMARY KEY,
  hotel_id BIGINT NOT NULL REFERENCES demo.hotels(hotel_id),
  room_type_id BIGINT NOT NULL REFERENCES demo.room_types(room_type_id),
  room_number VARCHAR(12) NOT NULL,
  floor INTEGER,
  status VARCHAR(20) NOT NULL,
  CONSTRAINT uq_room UNIQUE (hotel_id, room_number)
);
CREATE TABLE demo.guests (
  guest_id BIGINT PRIMARY KEY,
  first_name VARCHAR(80) NOT NULL,
  last_name VARCHAR(80) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  phone VARCHAR(40),
  date_of_birth DATE,
  created_at TIMESTAMP NOT NULL
);
CREATE TABLE demo.guest_addresses (
  guest_address_id BIGINT PRIMARY KEY,
  guest_id BIGINT NOT NULL REFERENCES demo.guests(guest_id),
  address_line1 VARCHAR(180) NOT NULL,
  city VARCHAR(80) NOT NULL,
  state VARCHAR(40),
  postal_code VARCHAR(20),
  country VARCHAR(60) NOT NULL
);
CREATE TABLE demo.loyalty_accounts (
  loyalty_account_id BIGINT PRIMARY KEY,
  guest_id BIGINT NOT NULL REFERENCES demo.guests(guest_id),
  member_number VARCHAR(24) NOT NULL UNIQUE,
  loyalty_tier VARCHAR(20) NOT NULL,
  points_balance INTEGER NOT NULL CHECK (points_balance >= 0)
);
CREATE TABLE demo.rate_plans (
  rate_plan_id BIGINT PRIMARY KEY,
  hotel_id BIGINT NOT NULL REFERENCES demo.hotels(hotel_id),
  name VARCHAR(80) NOT NULL,
  cancellation_policy VARCHAR(80),
  discount_percent DECIMAL(5,2) CHECK (discount_percent >= 0)
);
CREATE TABLE demo.availability (
  availability_id BIGINT PRIMARY KEY,
  room_id BIGINT NOT NULL REFERENCES demo.rooms(room_id),
  stay_date DATE NOT NULL,
  is_available BOOLEAN NOT NULL,
  nightly_rate DECIMAL(10,2) NOT NULL CHECK (nightly_rate >= 0),
  CONSTRAINT uq_room_date UNIQUE (room_id, stay_date)
);
CREATE TABLE demo.reservations (
  reservation_id BIGINT PRIMARY KEY,
  guest_id BIGINT NOT NULL REFERENCES demo.guests(guest_id),
  room_id BIGINT NOT NULL REFERENCES demo.rooms(room_id),
  rate_plan_id BIGINT REFERENCES demo.rate_plans(rate_plan_id),
  confirmation_code VARCHAR(20) NOT NULL UNIQUE,
  check_in_date DATE NOT NULL,
  check_out_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  total_amount DECIMAL(12,2) NOT NULL CHECK (total_amount >= 0),
  created_at TIMESTAMP NOT NULL
);
CREATE TABLE demo.reservation_guests (
  reservation_guest_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES demo.reservations(reservation_id),
  guest_id BIGINT NOT NULL REFERENCES demo.guests(guest_id),
  guest_role VARCHAR(20) NOT NULL
);
CREATE TABLE demo.payments (
  payment_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES demo.reservations(reservation_id),
  amount DECIMAL(12,2) NOT NULL CHECK (amount >= 0),
  currency VARCHAR(3) NOT NULL,
  payment_method VARCHAR(30) NOT NULL,
  payment_status VARCHAR(30) NOT NULL,
  paid_at TIMESTAMP
);
CREATE TABLE demo.refunds (
  refund_id BIGINT PRIMARY KEY,
  payment_id BIGINT NOT NULL REFERENCES demo.payments(payment_id),
  amount DECIMAL(12,2) NOT NULL CHECK (amount >= 0),
  reason VARCHAR(120),
  refunded_at TIMESTAMP
);
CREATE TABLE demo.cancellations (
  cancellation_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES demo.reservations(reservation_id),
  cancellation_date DATE NOT NULL,
  reason VARCHAR(120),
  fee_amount DECIMAL(12,2) CHECK (fee_amount >= 0)
);
CREATE TABLE demo.invoices (
  invoice_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES demo.reservations(reservation_id),
  invoice_number VARCHAR(30) NOT NULL UNIQUE,
  subtotal DECIMAL(12,2) NOT NULL CHECK (subtotal >= 0),
  tax_amount DECIMAL(12,2) NOT NULL CHECK (tax_amount >= 0),
  total_amount DECIMAL(12,2) NOT NULL CHECK (total_amount >= 0),
  status VARCHAR(20) NOT NULL
);
CREATE TABLE demo.invoice_lines (
  invoice_line_id BIGINT PRIMARY KEY,
  invoice_id BIGINT NOT NULL REFERENCES demo.invoices(invoice_id),
  description VARCHAR(180) NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 1),
  unit_price DECIMAL(12,2) NOT NULL CHECK (unit_price >= 0),
  line_total DECIMAL(12,2) NOT NULL CHECK (line_total >= 0)
);
CREATE TABLE demo.services (
  service_id BIGINT PRIMARY KEY,
  hotel_id BIGINT NOT NULL REFERENCES demo.hotels(hotel_id),
  name VARCHAR(100) NOT NULL,
  category VARCHAR(40) NOT NULL,
  unit_price DECIMAL(12,2) NOT NULL CHECK (unit_price >= 0)
);
CREATE TABLE demo.service_bookings (
  service_booking_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES demo.reservations(reservation_id),
  service_id BIGINT NOT NULL REFERENCES demo.services(service_id),
  quantity INTEGER NOT NULL CHECK (quantity >= 1),
  service_date DATE NOT NULL,
  status VARCHAR(20) NOT NULL
);
CREATE TABLE demo.employees (
  employee_id BIGINT PRIMARY KEY,
  hotel_id BIGINT NOT NULL REFERENCES demo.hotels(hotel_id),
  first_name VARCHAR(80) NOT NULL,
  last_name VARCHAR(80) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  role VARCHAR(40) NOT NULL,
  hire_date DATE NOT NULL,
  is_active BOOLEAN NOT NULL
);
