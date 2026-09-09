import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import date

# SQLAlchemy base class for declarative mapping
Base = declarative_base()

# Define the 3 tables
class Department(Base):
    __tablename__ = 'departments'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=False)

class Employee(Base):
    __tablename__ = 'employees'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    age = Column(Integer)
    salary = Column(Float)
    department_id = Column(Integer, ForeignKey('departments.id'))

class Sale(Base):
    __tablename__ = 'sales'
    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    sale_date = Column(Date, nullable=False)
    employee_id = Column(Integer, ForeignKey('employees.id'))

def setup_database(db_path="sqlite:///company.db"):
    """
    Creates the database schema and populates it with realistic mock data.
    """
    print(f"🔧 Creating database at {db_path}...")
    engine = create_engine(db_path)
    
    # Create all tables in the engine
    Base.metadata.create_all(engine)
    
    # Create a session to insert data
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Check if data already exists to avoid duplicate inserts
    if session.query(Department).first():
        print("✅ Database already populated. Skipping insertion.")
        return

    # Insert Departments
    d1 = Department(name="Engineering", location="New York")
    d2 = Department(name="Sales", location="London")
    d3 = Department(name="Marketing", location="San Francisco")
    session.add_all([d1, d2, d3])
    session.commit() # Commit to get the auto-generated IDs

    # Insert Employees
    e1 = Employee(name="Alice", age=28, salary=95000.0, department_id=d1.id)
    e2 = Employee(name="Bob", age=35, salary=75000.0, department_id=d2.id)
    e3 = Employee(name="Charlie", age=42, salary=110000.0, department_id=d1.id)
    e4 = Employee(name="David", age=25, salary=60000.0, department_id=d2.id)
    e5 = Employee(name="Eve", age=30, salary=80000.0, department_id=d3.id)
    session.add_all([e1, e2, e3, e4, e5])
    session.commit()

    # Insert Sales Records (mostly for the Sales and Marketing team)
    s1 = Sale(amount=15000.0, sale_date=date(2023, 1, 15), employee_id=e2.id)
    s2 = Sale(amount=23000.0, sale_date=date(2023, 2, 20), employee_id=e2.id)
    s3 = Sale(amount=8500.0,  sale_date=date(2023, 3, 10), employee_id=e4.id)
    s4 = Sale(amount=12000.0, sale_date=date(2023, 4, 5), employee_id=e5.id)
    s5 = Sale(amount=45000.0, sale_date=date(2023, 5, 22), employee_id=e2.id)
    session.add_all([s1, s2, s3, s4, s5])
    session.commit()
    
    session.close()
    print("✅ Successfully populated database with mock data.")

if __name__ == "__main__":
    setup_database()
