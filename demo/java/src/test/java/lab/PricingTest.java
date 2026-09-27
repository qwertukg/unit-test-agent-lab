package lab;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class PricingTest {
    @Test void regularTotal() {
        assertEquals(1000, new Pricing().total(1000, false, null));
    }
}
