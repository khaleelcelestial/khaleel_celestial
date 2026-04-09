import styles from "./ProfileCard.module.css";

function ProfileCard({ name, title, bio, avatarUrl }) {
  return (
    <div className={styles.card}>
      <img src={avatarUrl} alt={name} className={styles.avatar} />
      <h2 className={styles.name}>{name}</h2>
      <p className={styles.title}>{title}</p>
      <p className={styles.bio}>{bio}</p>
    </div>
  );
}

export default ProfileCard;
