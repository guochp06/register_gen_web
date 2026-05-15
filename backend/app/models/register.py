"""
Register models - Module, Register, Field
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, BigInteger
from sqlalchemy.orm import relationship, backref
from app.db.base import Base

class RegisterModule(Base):
    __tablename__ = "register_modules"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("versions.id"), nullable=False)
    name = Column(String(100), nullable=False)
    base_address = Column(BigInteger, default=0)
    end_address = Column(BigInteger, default=0)
    size = Column(BigInteger, default=0)
    parent_module_id = Column(Integer, ForeignKey("register_modules.id"), nullable=True)
    is_array = Column(Integer, default=0)  # 0 or 1
    array_count = Column(Integer, default=1)

    version = relationship("Version", back_populates="modules")
    registers = relationship("Register", back_populates="module", cascade="all, delete-orphan")
    parent = relationship("RegisterModule", remote_side=[id], backref=backref("submodules", cascade="all, delete-orphan"))

class Register(Base):
    __tablename__ = "registers"

    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("register_modules.id"), nullable=False)
    name = Column(String(100), nullable=False)
    address = Column(BigInteger, nullable=False)
    offset = Column(BigInteger, default=0)
    width = Column(Integer, default=32)
    description = Column(Text, nullable=True)
    is_array = Column(Integer, default=0)
    array_count = Column(Integer, default=1)

    module = relationship("RegisterModule", back_populates="registers")
    fields = relationship("RegisterField", back_populates="register", cascade="all, delete-orphan")

class RegisterField(Base):
    __tablename__ = "register_fields"

    id = Column(Integer, primary_key=True, index=True)
    register_id = Column(Integer, ForeignKey("registers.id"), nullable=False)
    name = Column(String(100), nullable=False)
    bit_range = Column(String(50), nullable=False)
    msb = Column(Integer, nullable=False)
    lsb = Column(Integer, nullable=False)
    access = Column(String(20), default="RW")
    reset_value = Column(String(50), default="0")
    description = Column(Text, nullable=True)

    register = relationship("Register", back_populates="fields")
