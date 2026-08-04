from app.database.product_repository import ProductRepository

repository = ProductRepository()

product = repository.public_info("G81U6")
print(product)