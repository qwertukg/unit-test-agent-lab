package lab;

/** Synthetic discount rules; no corporate source. */
public class Pricing {
    public int total(int cents, boolean member, String coupon) {
        if (cents < 0) {
            throw new IllegalArgumentException("negative amount");
        }
        if (coupon != null && !coupon.equals("SAVE10")) {
            throw new IllegalArgumentException("unknown coupon");
        }
        int discount = "SAVE10".equals(coupon) ? 10 : 0;
        if (member && cents >= 10000) {
            discount += 5;
        }
        return cents * (100 - discount) / 100;
    }
}
