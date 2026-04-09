import styles from "./ProductCard.module.css";

function ProductCard({ image, name, price, description, onAddToCart }) {
  return (
    <div className={styles.card}>
      <img src={image} alt={name} className={styles.image} />
      <div className={styles.body}>
        <h3 className={styles.name}>{name}</h3>
        <p className={styles.description}>{description}</p>
        <div className={styles.footer}>
          <span className={styles.price}>₹{price}</span>
          <button className={styles.btn} onClick={() => onAddToCart(name)}>
            Add to Cart
          </button>
        </div>
      </div>
    </div>
  );
}

export default ProductCard;
