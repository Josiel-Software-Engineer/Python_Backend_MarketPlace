from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestFrom, OAuth2PasswordBearer
from jose import JWTError, JWTError
from sqlalchemy.orm import Session
from typing import List
from app.core.config import settings
from app.core.security import verify_password, create_access_token
from app.schemas.auth import LoginSchema
from app.schemas.product import ProductCreate, ProductResponse
from app.schemas.order import OrderCreate, OrderResponse, OrderStatusUpdate
from app.models.user import User
from app.models.product import Product
from app.models.order import Order, OrderItem
from app.database.connection import get_db, engine, Base

oath2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_curret_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Token inválido")

    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado!")
    return user

# Create Tables
Base.metadata.create_all(bind=engine)
app = FastAPI(title="<STORE_NAME>")

# ROUTES

# 1. Products List Route (GET)
@app.get("/products", response_model=List[ProductResponse])
def get_products(db: Session = Depends(get_db)):
    return db.query(Product).all()

# 2. History Route
@app.get("/orders/me", response_model=List[OrderResponse])
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_curret_user)
):
    # 2.1 Returns requests from the logged in user
    return db.query(Order).filter(Order.user_id == current_user.id).all()

# 3. Route to CREATE a product (POST)
@app.post("/products", response_model=ProductResponse)
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_curret_user)
):
    # 3.1 .dict() has been replaced by .model_dump() in Pydantic V2 (more modern)
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commint()
    db.refresh(db_product)
    return db_product

# 4. Login security
@app.post("/auth/login")
def login(
    form_data: OAuth2PasswordRequestFrom = Depends(),
    db: Session = Depends(get_db)
):
    #4.1 Search the user in the database
    user = db.query(User).filter(User.email == form_data.username).first()

    #4.2 Check if it exists and if the password matches
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos!"
        )
    #4.3 Creates the access token
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

# 5. Route to UPDATE a product (PUT)
@app.put("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_curret_user)
):
    db_product = db.query(Product).filter(Product.id == product_id).first()
    
    if not db_product:
        raise HTTPException(status_code=404, detail="Produto não encontrado no estoque!")

    for key, value in product_data.model_dump().items():
        setattr(db_product, key, value)

    db.commint()
    db.refresh(db_product)

    return db_product

# 6. Route to DELETE a product (DELETE)
@app.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_curret_user)
):
    # 6.1 Search the product by ID
    db_product = db.query(Product).filter(Product.id == product_id).first()

    # 6.2 If not found, returns 404
    if not db_product:
        raise HTTPException(
            status_code=404,
            detail="Produto não encontrado! É provável que já tenha sido removido."
        )
    # 6.3 Remove from the database and save the change
    db.delete(db_product)
    db.commint()

    return {"message": f"Produto {product_id} deletado com sucesso!"}

# 7. Calculation Logic
@app.post("/orders", response_model=OrderResponse)
def create_order(
    order_data: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    total_price = 0
    order_items_to_create = []
    
    # 7.1 Validate products and calculate the total.
    for item in order_data.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Produto {item.product_id} não existe!")
        
        # 7.1.1 NEW INVENTORY LOGIC
        if product.stock < item.quantity:
            raise HTTPException(
                status_code=400, 
                detail=f"Estoque insuficiente para o produto {product.name}. Disponível: {product.st>
            )
        
        # 7.1.2 Subtract from stock
        product.stock -= item.quantity

        # 7.1.3 Calculates the total order value
        item_total = product.price * item.quantity
        total_price += item_total
        
        # 7.1.4 Prepares the item to be saved later
        order_items_to_create.append({
            "product_id": product.id,
            "quantity": item.quantity,
            "price_unit": product.price
        })

    # 7.2 Create the Order (Header)
    new_order = Order(
        user_id=current_user.id,
        total_price=total_price,
        status="pendente"
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    
    # 7.3 Create Order items (Details)
    for item_data in order_items_to_create:
        db_item = OrderItem(
            order_id=new_order.id,
            **item_data
        )
        db.add(db_item)
        
    db.commit()
    db.refresh(new_order)
    
    return new_order

# 8. Order status update
@app.patch("/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    
    # 8.1 Search the order by ID
    order = db.query(Order).filter(Order.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado!")
    
    novo_status = status_data.status
    
    # 8.3 RULE 1: THE CUSTOMER ONLY CANCELS IF IT IS PENDING
    if not current_user.is_admin:

        # 8.3.1 If it is not admin, it can only try to cancel
        if novo_status == "cancelado":
            if order.status != "pendente":
                raise HTTPException(
                    status_code=400,
                    detail="Você só pode cancelar pedido que ainda está 'pendente'!"
                )
            # 8.3.1.1 Check if the order is his own
            if order.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Acesso negado!")
        
        else:
            # 8.3.2 Customer tries to change status to "ready", "sent", etc.
            raise HTTPException(
                status_code=403,
                detail="Apenas administradores podem alterar o status para " + novo_status
            )
            
    # 8.4 RULE 2: CANCELLATION LOGIC (SAME AS PREVIOUS)
    if novo_status == "cancelado" and order.status != "cancelado":
        for item in order.items:
            product = db.query(Product).filter(Product.id == item.product_id).first()
            if product:
                product.stock += item.quantity
                
    # 8.5 Update only the status field
    order.status = novo_status
    
    db.commit()
    db.refresh(order)
    return order
    