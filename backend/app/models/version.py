"""
Version model
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base

class Version(Base):
    __tablename__ = "versions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    html_path = Column(String(500), nullable=True)  # HTML输出路径
    warnings = Column(JSON, nullable=True)  # 存储生成过程中的warnings
    top_addrmap_name = Column(String(100), nullable=True)  # 顶层addr_map名字（从Excel解析）
    user_id = Column(String(100), nullable=False, default='default', index=True)
    is_published = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    modules = relationship("RegisterModule", back_populates="version",
                          cascade="all, delete-orphan", passive_deletes=True)
