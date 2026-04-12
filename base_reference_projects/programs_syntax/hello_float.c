
// This is a very silly demo program
void main(){
    //think about it
}

// A space is used to declare an explicit memory stack space, to allow shared memories without requiring nested arguments to functions
// Spaces declaration can be recursive
space matrix {    
    public float32[32,8,8] numbers;
    float32[32] average;


    void calculate(){
        for(int i=0; i<8; i++){
            for(int j=0; j<32; j++){
                in_matrix imatrx;
                imatrx.sub_numbers = &numbers[j];
                average[j] = recalculate();
            }
        }
    }

    space in_matrix {
        float32[8,8]* sub_numbers;

        float32 recalculate(){
            float32 avg = 0.0f;
            for(int i=0; i<8; i++){
                for(int j=0; j<8; j++){
                    sub_numbers[i,j] = ...
                    //...                    
                }

                avg = sub_numbers[..] + ... / ...
            }

            return avg
        }
    }
}