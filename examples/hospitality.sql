CREATE TABLE prod.guests (
  guest_id BIGINT PRIMARY KEY,
  first_name VARCHAR(80) NOT NULL,
  last_name VARCHAR(80) NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL,
  loyalty_tier VARCHAR(30)
);

CREATE TABLE prod.reservations (
  reservation_id BIGINT PRIMARY KEY,
  guest_id BIGINT NOT NULL REFERENCES prod.guests(guest_id),
  check_in_date DATE NOT NULL,
  check_out_date DATE NOT NULL,
  status VARCHAR(30) NOT NULL,
  nightly_rate DECIMAL(10,2) NOT NULL
);

CREATE TABLE prod.payments (
  payment_id BIGINT PRIMARY KEY,
  reservation_id BIGINT NOT NULL REFERENCES prod.reservations(reservation_id),
  amount DECIMAL(10,2) NOT NULL,
  currency VARCHAR(3) NOT NULL,
  payment_status VARCHAR(30) NOT NULL
);
